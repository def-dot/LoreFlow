"""
LLM 公共调用层 — 统一 OpenAI 兼容接口，支持多 provider 后端。

模型引用格式：
- ``provider:model`` → 指定 provider 和模型
- ``provider`` → 使用该 provider 的默认模型
- ``其他`` → 视为裸模型名，使用 default_model 对应的 provider

配置从 ``providers.yml`` 读取，示例见项目根目录。
"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)


def _read_providers() -> dict[str, Any]:
    """读取并返回 providers.yml 内容。"""
    path = settings.PROVIDERS_FILE
    if not path.is_file():
        raise ValueError(f"Provider 配置文件不存在: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_models() -> dict[str, list[str]]:
    """返回每个 provider 的可用模型列表。"""
    cfg = _read_providers()
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
    cfg = _read_providers()

    # 解析 model_str → provider_name + model_name
    model = model or cfg.get("default_model", "")
    if not model:
        raise ValueError("未指定模型且 providers.yml 未配置 default_model")
    
    provider_name, model_name = model.split(":", 1)

    provider = cfg.get(provider_name)
    if not isinstance(provider, dict):
        raise ValueError(f"Unknown provider: {provider_name}")

    base_url = str(provider.get("base_url", "")).rstrip("/")
    api_key = str(provider.get("api_key", ""))

    # 发请求
    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": False}
    if tools is not None:
        payload["tools"] = tools

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
