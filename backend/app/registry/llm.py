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


async def _ollama_chat(model: str, messages: list[dict[str, str]]) -> str:
    """POST /api/chat（非流式）→ 助手回复文本。

    连接层错误（服务未启动/超时）与 HTTP 错误（模型不存在等）分别给出
    可定位的中文提示，底层异常链保留在 ``__cause__``。
    """
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    # trust_env=False：本地服务不走系统代理（否则代理拦截回环流量会得到莫名 502）；
    # 连接 10s 快速失败，读超时（等模型生成）放宽到 OLLAMA_TIMEOUT_SECONDS
    timeout = httpx.Timeout(10.0, read=settings.OLLAMA_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            resp = await client.post(url, json={"model": model, "messages": messages, "stream": False})
    except httpx.HTTPError as exc:
        raise ValueError(f"无法连接 Ollama（{url}）：{exc}") from exc
    if resp.status_code != 200:
        raise ValueError(f"Ollama 返回 {resp.status_code}（模型 {model!r}）：{resp.text[:200]}")
    try:
        return str(resp.json()["message"]["content"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Ollama 响应格式异常：{resp.text[:200]!r}") from exc


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
