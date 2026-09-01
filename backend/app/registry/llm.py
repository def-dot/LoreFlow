"""
LLM 节点 — 调用本地 Ollama 服务（默认 http://localhost:11434）。

输入约定：节点函数从共享上下文取值（YAML 顶层 ``inputs`` 声明 + 创建
运行时提供），模型名可被 ``ctx["model"]`` 覆盖，缺省取 ``settings.OLLAMA_MODEL``。
连接/超时/模型不存在统一转成中文 ValueError，节点结果里直接可读。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.registry.core import node_type
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


@node_type(
    label="LLM 对话",
    description=(
        "读取 ctx['prompt'] 调用本地 Ollama 生成回答"
    ),
)
async def llm_chat(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML inputs 声明为必填，创建运行时提供）")
    messages: list[dict[str, str]] = []
    system = ctx.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    model = str(ctx.get("model") or settings.OLLAMA_MODEL)
    return await _ollama_chat(model, messages)


@node_type(
    label="意图识别",
    description="通用意图分类器",
)
async def llm_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串")

    system = ctx.get("classify_system")
    if not isinstance(system, str) or not system.strip():
        system = (
            "你是意图分类器，只允许输出以下标签之一，"
            "禁止输出其他任何内容（包括标点、解释或其他语言）：\n"
            "chat —— 问候、闲聊、创作、翻译等无需查询资料的请求\n"
            "rag —— 需要查询知识库/设定资料才能回答的问题\n"
            "search —— 需要联网搜索最新信息才能回答（时事新闻、实时数据等）\n"
            "human —— 需要转人工客服"
        )

    labels = ctx.get("classify_labels")
    if not isinstance(labels, list) or not labels:
        labels = ["chat", "rag", "search", "human"]

    raw = await _ollama_chat(
        settings.OLLAMA_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        fmt={"type": "string", "enum": labels},
    )
    text = raw.strip().lower()
    return {"intent": text, "raw": raw.strip()}


@node_type(
    label="知识库问答",
    description="结合检索片段回答 ctx['prompt']；检索结果 chunks ← rag_retrieve 类节点（YAML inputs 接线，未接线或上游被跳过视为未检索到）",
)
async def llm_rag_reply(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML inputs 声明为必填，创建运行时提供）")
    chunks = ctx.get("chunks") or []
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


@node_type(
    label="搜索汇总",
    description="读取 ctx['prompt'] 和 ctx['search']（web_search 输出），将搜索结果附在问题后交给 LLM 总结回复",
)
async def search_summarize(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串")
    results = ctx.get("search") or []
    if not results:
        return await llm_chat(ctx)
    refs = "\n\n".join(
        f"[{i}] {r.get('title', '')}\n{r.get('url', '')}\n{r.get('snippet', '')}"
        for i, r in enumerate(results, 1)
        if isinstance(r, dict)
    )
    system = "你是一个有帮助的助手。根据搜索结果回答用户问题；如果搜索结果不足以回答，基于你的知识回答并说明。"
    user = f"搜索结果：\n{refs}\n\n用户问题：{prompt}"
    model = str(ctx.get("model") or settings.OLLAMA_MODEL)
    return await _ollama_chat(model, [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])


@node_type(
    label="最终答复",
    description=(
        "互斥分支汇合：按 depends_on 声明顺序取第一个非空上游输出（视图保留键 "
        "_upstream，无需 inputs 接线），输出 {branch, answer}（branch = 支路节点名）；"
        "未执行的支路输出为 null"
    ),
)
async def final_answer(ctx: dict[str, Any]) -> dict[str, Any]:
    upstream = ctx.get("_upstream")
    if not isinstance(upstream, dict) or not upstream:
        raise ValueError("缺少上游支路：final_answer 须声明 depends_on（按声明顺序取第一个非空输出）")
    for _, answer in upstream.items():
        if answer:
            return answer
    raise ValueError("没有支路产出答复：上游输出全为空（互斥条件可能全部未命中）")
