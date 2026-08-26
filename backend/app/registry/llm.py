"""
LLM 节点 — 调用本地 Ollama 服务（默认 http://localhost:11434）。

输入约定：节点函数从共享上下文取值（YAML ``params`` 声明 + 创建运行时
提供），模型名可被 ``ctx["model"]`` 覆盖，缺省取 ``settings.OLLAMA_MODEL``。
连接/超时/模型不存在统一转成中文 ValueError，节点结果里直接可读。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.registry.core import node
from app.utils.http import http_client


async def _ollama_chat(
    model: str, messages: list[dict[str, str]], fmt: dict[str, Any] | str | None = None
) -> str:
    """POST /api/chat（非流式）→ 助手回复文本；fmt 透传 Ollama 的 format
    约束（``"json"`` 或 JSON Schema，如分类用的 enum）。"""
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if fmt is not None:
        payload["format"] = fmt
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    resp = await http_client().post(url, json=payload)
    resp.raise_for_status()
    data = resp.json()

    content = data["message"]["content"]
    return str(content)


@node(
    label="LLM 对话",
    description=(
        "读取 ctx['prompt'] 调用本地 Ollama 生成回答"
    ),
)
async def llm_chat(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML params 声明为必填，创建运行时提供）")
    content = prompt
    pages = ctx.get("pages")  # 上游 web_fetch 节点（YAML 里命名为 pages）的输出
    if isinstance(pages, list) and pages:
        refs = "\n\n".join(
            f"[网页 {i}] {page.get('url')}\n{page.get('text', '')}"
            for i, page in enumerate(pages, 1)
            if isinstance(page, dict)
        )
        content = f"{prompt}\n\n---\n以下是提示词中链接对应的网页内容，回答时请参考：\n\n{refs}"
    messages: list[dict[str, str]] = []
    system = ctx.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    model = str(ctx.get("model") or settings.OLLAMA_MODEL)
    return await _ollama_chat(model, messages)


@node(
    label="意图识别",
    description="LLM 判断 ctx['prompt'] 是闲聊（chat）还是知识类问题（rag），输出 {intent, raw}；提示词包含「人工」直接判 human（转人工，无需模型）",
)
async def llm_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML params 声明为必填，创建运行时提供）")
    # 关键词短路：用户点名「人工」直接转人工——优先级最高且确定性，不必过模型
    if "人工" in prompt:
        return {"intent": "human", "raw": "关键词命中：人工"}
    system = (
        "你是客服意图分类器，只允许输出 chat 或 rag 两个单词之一，"
        "禁止输出其他任何内容（包括标点、解释或其他语言）：\n"
        "chat —— 问候、闲聊、创作、翻译等无需查询资料的请求\n"
        "rag —— 需要查询知识库/设定资料才能回答的问题"
    )
    raw = await _ollama_chat(
        settings.OLLAMA_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        # 硬约束：服务端按 schema 采样，只会吐出 enum 里的词
        fmt={"type": "string", "enum": ["chat", "rag"]},
    )
    text = raw.lower()
    intent = "rag" if any(k in text for k in ("rag", "知识", "检索", "问答")) else "chat"
    return {"intent": intent, "raw": raw.strip()}


def _latest(ctx: dict[str, Any], shape: Any) -> Any:
    """倒序找最近一个符合形状的上游输出（节点名即 ctx 键，中文命名不影响接线），
    找不到返回 None。与 rag._find_upstream 同款，条件侧缺上游按 False 处理。"""
    for value in reversed(list(ctx.values())):
        if shape(value):
            return value
    return None


@node(kind="condition", label="意图判定", description="意图识别输出的 intent 等于给定值（condition: {fn: intent_is, value: chat|rag|human}；按 {intent} 形状找上游，节点中英文命名均可）")
def intent_is(ctx: dict[str, Any], value: str) -> bool:
    classify = _latest(ctx, lambda v: isinstance(v, dict) and "intent" in v)
    return classify is not None and classify.get("intent") == value


@node(
    label="知识库问答",
    description="结合检索片段回答 ctx['prompt']（按 [{source, text}] 形状找上游检索输出，节点中英文命名均可）",
)
async def llm_rag_reply(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML params 声明为必填，创建运行时提供）")
    chunks = _latest(
        ctx,
        lambda v: isinstance(v, list) and bool(v) and all(isinstance(c, dict) and "text" in c for c in v),
    ) or []
    if not chunks:
        return "知识库中没有检索到相关内容。"
    refs = "\n\n".join(
        f"[{i}] {c.get('source', '')} {c.get('text', '')}"
        for i, c in enumerate(chunks, 1)
        if isinstance(c, dict)
    )
    system = "你是知识库问答助手。仅依据参考资料回答问题；资料不足以回答时明确说明，不要编造。"
    user = f"参考资料：\n{refs}\n\n问题：{prompt}"
    return await _ollama_chat(
        settings.OLLAMA_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
