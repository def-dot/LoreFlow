"""
LLM 公共调用层 — 统一 OpenAI 兼容接口，支持 Ollama / MiMo 等后端。

模型引用格式：
- ``provider:model`` → 指定 provider 和模型
- ``provider`` → 使用该 provider 的默认模型
- 其他 → ollama + 原始字符串作为模型名
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# provider 配置
# ---------------------------------------------------------------------------

_PROVIDER_CONFIGS: dict[str, dict[str, str]] = {
    "ollama": {
        "base_url": settings.OLLAMA_BASE_URL,
        "api_key": "",
        "default_model": "qwen2.5:latest",
    },
    "mimo": {
        "base_url": settings.MIMO_BASE_URL,
        "api_key": settings.MIMO_API_KEY,
        "default_model": "mimo-v2.5-pro",
    },
}


async def llm_chat_call(
    model: str,
    messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """POST /chat/completions（非流式）→ ``{"content": str, "tool_calls": list}``。

    模型引用格式：``provider:model``、``provider``（用默认模型）、或裸模型名（ollama）。
    """
    # 解析 provider:model
    if model in _PROVIDER_CONFIGS:
        provider, model_name = model, _PROVIDER_CONFIGS[model]["default_model"]
    elif ":" in model and model.partition(":")[0] in _PROVIDER_CONFIGS:
        provider, model_name = model.partition(":")[0], model.partition(":")[2]
    else:
        provider, model_name = "ollama", model

    cfg = _PROVIDER_CONFIGS[provider]
    if provider == "mimo" and not cfg["api_key"]:
        raise ValueError("MiMo API Key 未配置（MIMO_API_KEY）")

    payload: dict[str, Any] = {"model": model_name, "messages": messages, "stream": False}
    if tools is not None:
        payload["tools"] = tools

    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers: dict[str, str] = {}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    logger.info("[llm] %s %s", provider, model_name)
    resp = await http_client().post(url, json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]["message"]
    raw_calls = choice.get("tool_calls") or []
    tool_calls = [
        {"function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}
        for tc in raw_calls
    ]
    return {
        "content": str(choice.get("content", "")),
        "tool_calls": tool_calls,
    }
