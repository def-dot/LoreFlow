"""
LLM 节点 — 调用本地 Ollama 服务（默认 http://localhost:11434）。

输入约定：节点函数从共享上下文取值（YAML ``params`` 声明 + 创建运行时
提供），模型名可被 ``ctx["model"]`` 覆盖，缺省取 ``settings.OLLAMA_MODEL``。
连接/超时/模型不存在统一转成中文 ValueError，节点结果里直接可读。
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.registry.core import node


async def _ollama_chat(
    model: str, 
    messages: list[dict[str, str]]
) -> str:
    """POST /api/chat（非流式）→ 助手回复文本。"""
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"

    try:
        async with httpx.AsyncClient(timeout=settings.OLLAMA_TIMEOUT_SECONDS) as http_client:
            resp = await http_client.post(
                url, 
                json={"model": model, "messages": messages, "stream": False}
            )
            resp.raise_for_status()
            
            data = resp.json()
            content = data.get("message", {}).get("content")
            if content is None:
                raise ValueError(f"Ollama 响应格式异常：{resp.text[:200]!r}")
                
            return str(content)
    except httpx.HTTPStatusError as exc:
        raise ValueError(
            f"Ollama 返回 {exc.response.status_code}（模型 {model!r}）：{exc.response.text[:200]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"无法连接 Ollama（{url}）：{exc}") from exc


@node(
    label="LLM 对话",
    description="读取 ctx['prompt'] 调用本地 Ollama 生成回答；可选 ctx['system'] 设系统提示、ctx['model'] 覆盖模型",
)
async def llm_chat(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML params 声明为必填，创建运行时提供）")
    messages: list[dict[str, str]] = []
    system = ctx.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    model = str(ctx.get("model") or settings.OLLAMA_MODEL)
    return await _ollama_chat(model, messages)
