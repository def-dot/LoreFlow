"""
LLM 公共调用层 — 统一 OpenAI 兼容接口，支持多 provider 后端。

模型引用格式：
- ``provider_name/model_name`` → 指定 provider 和模型
- ``None`` → 使用 settings 表中 default_chat_model 指定的模型

配置从数据库读取（providers + models + settings 表），由「模型管理」页面维护。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from app.utils.http import http_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

async def _resolve_chat_model(model_key: str | None) -> tuple[str, str, str]:
    """解析模型引用 → (base_url, api_key, model_name)。"""
    from app.core.database import AsyncSessionLocal
    from app.models.provider import ProviderRecord, SettingRecord
    from sqlmodel import select

    # model_key 为空时，从 settings 表读默认模型
    if not model_key:
        async with AsyncSessionLocal() as session:
            rec = (await session.exec(
                select(SettingRecord).where(SettingRecord.key == "default_chat_model")
            )).one_or_none()
        if not rec or not rec.value:
            raise ValueError("未配置默认模型，请在「模型管理」页面设置默认文本生成模型")
        model_key = rec.value.strip()

    provider_name, model_name = model_key.split("/", 1)

    async with AsyncSessionLocal() as session:
        p = (await session.exec(
            select(ProviderRecord).where(ProviderRecord.name == provider_name)
        )).one_or_none()
        if p:
            return p.base_url, p.api_key, model_name

    raise ValueError(f"Provider {provider_name!r} 不存在，请在「模型管理」页面添加")


# ---------------------------------------------------------------------------
# LLM 调用
# ---------------------------------------------------------------------------

async def llm_chat_call(
    model_key: str | None = None,
    messages: list[dict[str, str]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST /chat/completions（非流式）→ ``{"content": str, "tool_calls": list}``。"""
    if messages is None:
        raise ValueError("messages is required")
    base_url, api_key, model_name = await _resolve_chat_model(model_key)
    logger.info("[llm] %s %s", base_url, model_name)

    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": False}
    if tools is not None:
        payload["tools"] = tools
    if response_format is not None:
        payload["response_format"] = response_format

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info("[llm] %s", model_name)
    resp = await http_client().post(f"{base_url}/chat/completions", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]["message"]
    return {
        "content": str(choice.get("content", "")),
        "tool_calls": choice.get("tool_calls") or [],
    }


async def llm_chat_stream(
    model_key: str | None,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """POST /chat/completions（流式）→ 逐 chunk yield。"""
    base_url, api_key, model_name = await _resolve_chat_model(model_key)

    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": True}
    if tools is not None:
        payload["tools"] = tools

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info("[llm:stream] %s", model_name)

    tool_calls: dict[int, dict[str, Any]] = {}

    client = http_client()
    async with client.stream(
        "POST", f"{base_url}/chat/completions", json=payload, headers=headers
    ) as resp:
        if resp.status_code >= 400:
            body = await resp.aread()
            logger.error("[llm:stream] %d — body: %s", resp.status_code, body.decode(errors="replace"))
            resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            finish_reason = choices[0].get("finish_reason")

            content = delta.get("content") or ""
            reasoning = delta.get("reasoning_content") or ""
            raw_tool_calls = delta.get("tool_calls") or []

            for tc in raw_tool_calls:
                idx = tc.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                tool_calls[idx]["id"] = tool_calls[idx]["id"] or tc.get("id", "")
                tool_calls[idx]["function"]["name"] += tc.get("function", {}).get("name") or ""
                tool_calls[idx]["function"]["arguments"] += tc.get("function", {}).get("arguments") or ""

            yield {
                "content": content,
                "reasoning": reasoning,
                "tool_calls": [tool_calls[i] for i in sorted(tool_calls)] if finish_reason else [],
                "finish_reason": finish_reason,
            }