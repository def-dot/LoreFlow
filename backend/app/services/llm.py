"""
LLM 公共调用层 — 统一 OpenAI 兼容接口，支持多 provider 后端。

模型引用格式：
- ``provider:model`` → 指定 provider 和模型
- ``provider`` → 使用该 provider 的默认模型
- ``其他`` → 视为裸模型名，使用 default_model 对应的 provider

配置从 ``providers.yml`` 读取，示例见项目根目录。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import yaml

from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)


def list_models() -> dict[str, list[str]]:
    """返回每个 provider 的可用模型列表。"""
    with open(settings.PROVIDERS_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return {
        name: p.get("models", [])
        for name, p in cfg.items()
        if isinstance(p, dict)
    }


async def llm_chat_call(
    model: str | None,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """POST /chat/completions（非流式）→ ``{"content": str, "tool_calls": list}``。"""
    with open(settings.PROVIDERS_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    model = model or cfg.get("default_model", "")

    provider, model_name = model.split(":")
    provider_cfg = cfg.get(provider)

    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": False}
    if tools is not None:
        payload["tools"] = tools

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if provider_cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {provider_cfg['api_key']}"

    logger.info("[llm] %s", model_name)
    resp = await http_client().post(f"{provider_cfg['base_url']}/chat/completions", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]["message"]
    return {
        "content": str(choice.get("content", "")),
        "tool_calls": choice.get("tool_calls") or [],
    }


async def llm_chat_stream(
    model: str | None,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """POST /chat/completions（流式）→ 逐 chunk yield。
    """
    with open(settings.PROVIDERS_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    model = model or cfg.get("default_model", "")

    provider, model_name = model.split(":")
    provider_cfg = cfg.get(provider)

    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": True}
    if tools is not None:
        payload["tools"] = tools

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if provider_cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {provider_cfg['api_key']}"

    logger.info("[llm:stream] %s", model_name)

    tool_calls: dict[int, dict[str, Any]] = {}

    async with http_client().stream(
        "POST", f"{provider_cfg['base_url']}/chat/completions", json=payload, headers=headers
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            logger.info(f"data-------- {line}")
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            
            # delta = chunk.get("choices", [{}])[0].get("delta", {})
            # finish_reason = chunk["choices"][0].get("finish_reason")

            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            finish_reason = choices[0].get("finish_reason")

            content = delta.get("content") or ""
            raw_tool_calls = delta.get("tool_calls") or []
            
            """
            {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_abc","function":{"name":"ge"}}]}}]}
            {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"t_wea"}}]}}]}
            {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"ther","arguments":"{\""}}]}}]}
            {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"city"}}]}}]}
            {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\":\"北京\"}"}}]}}]}
            """
            for tc in raw_tool_calls:
                idx = tc.get("index", 0)
                if idx not in tool_calls:
                    tool_calls[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                tool_calls[idx]["id"] = tool_calls[idx]["id"] or tc.get("id", "")
                tool_calls[idx]["function"]["name"] += tc.get("function", {}).get("name") or ""
                tool_calls[idx]["function"]["arguments"] += tc.get("function", {}).get("arguments") or ""

            yield {
                "content": content,
                "tool_calls": [tool_calls[i] for i in sorted(tool_calls)] if finish_reason else [],
                "finish_reason": finish_reason,
            }
