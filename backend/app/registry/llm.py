"""
LLM 节点 — 调用本地 Ollama 服务（默认 http://localhost:11434）。

输入约定：节点函数从共享上下文取值（YAML 顶层 ``inputs`` 声明 + 创建
运行时提供），模型名可被 ``ctx["model"]`` 覆盖，缺省取 ``settings.OLLAMA_MODEL``。
连接/超时/模型不存在统一转成中文 ValueError，节点结果里直接可读。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.registry.core import NodeGroup, node_type
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
    group=NodeGroup.LLM,
    description="调用本地 Ollama 模型生成回答",
    input_schema={
        "prompt": {"type": "string", "required": True, "description": "用户提示词"},
        "system": {"type": "string", "required": False, "description": "系统提示词"},
        "context": {"type": "string", "required": False, "description": "上下文"},
        "model": {"type": "string", "required": False, "description": "模型名"},
    },
    output_schema={"type": "string", "description": "LLM 回复文本"},
)
async def llm_chat(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML inputs 声明为必填，创建运行时提供）")
    messages: list[dict[str, str]] = []
    system = ctx.get("system")
    context = ctx.get("context")
    if isinstance(context, str) and context.strip():
        prompt = f"参考资料：\n{context}\n\n用户问题：{prompt}"
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    model = str(ctx.get("model") or settings.OLLAMA_MODEL)
    return await _ollama_chat(model, messages)


@node_type(
    label="意图识别",
    description="通用意图分类器",
    group=NodeGroup.LLM,
    input_schema={
        "prompt": {"type": "string", "required": True, "description": "待分类文本"},
        "classify_system": {"type": "string", "required": False, "description": "分类系统提示词"},
        "classify_labels": {"type": "list", "item": {"type": "string"}, "required": False, "description": "可选标签列表"},
    },
    output_schema={
        "type": "object",
        "fields": {
            "intent": {"type": "string", "description": "分类标签"},
        },
    },
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
    text = raw.strip().strip('"').lower()
    return {"intent": text, "raw": raw.strip()}


@node_type(
    label="知识库问答",
    description="结合检索片段回答 ctx['prompt']；检索结果 chunks ← rag_retrieve 类节点（YAML inputs 接线，未接线或上游被跳过视为未检索到）",
    group=NodeGroup.LLM,
    input_schema={
        "prompt": {"type": "string", "required": True, "description": "用户问题"},
        "chunks": {"type": "list", "required": False, "description": "检索片段列表（来自 rag_retrieve）"},
    },
    output_schema={"type": "string", "description": "基于知识库的回答"},
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
    label="最终答复",
    group=NodeGroup.LLM,
    description=(
        "互斥分支汇合：按 depends_on 声明顺序取第一个非空上游输出（视图保留键 "
        "_upstream，无需 inputs 接线），输出 {branch, answer}（branch = 支路节点名）；"
        "未执行的支路输出为 null"
    ),
    input_schema={},
    output_schema={"type": "any", "description": "第一个非空上游输出（原样透传）"},
)
async def final_answer(ctx: dict[str, Any]) -> dict[str, Any]:
    upstream = ctx.get("_upstream")
    if not isinstance(upstream, dict) or not upstream:
        raise ValueError("缺少上游支路：final_answer 须声明 depends_on（按声明顺序取第一个非空输出）")
    for _, answer in upstream.items():
        if answer:
            return answer
    raise ValueError("没有支路产出答复：上游输出全为空（互斥条件可能全部未命中）")
