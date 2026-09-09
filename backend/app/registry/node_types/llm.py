"""
LLM 节点 — llm_chat / llm_classify，调用 app.services.llm 的公共接口。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.llm import llm_chat_call
from app.registry.node_type import NodeGroup, node_type


@node_type(
    label="LLM 对话",
    group=NodeGroup.LLM,
    description="调用 LLM 模型生成回答，支持 Ollama / MiMo 等 OpenAI 兼容后端",
    input_schema={
        "prompt": {"type": "string", "required": True, "description": "用户提示词"},
        "system": {"type": "string", "required": False, "description": "系统提示词"},
        "context": {"type": "string", "required": False, "description": "上下文"},
        "model": {"type": "string", "required": False, "description": "模型名"},
        "tools": {
            "type": "list",
            "required": False,
            "description": "工具定义列表（OpenAI function calling 格式）",
        },
    },
    output_schema={
        "type": "object",
        "fields": {
            "content": {"type": "string", "description": "LLM 回复文本"},
            "tool_calls": {
                "type": "list",
                "description": "模型请求的工具调用列表（未传 tools 时为空）",
            },
        },
    },
)
async def llm_chat(ctx: dict[str, Any]) -> dict[str, Any]:
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
    model = str(ctx.get("model") or settings.DEFAULT_MODEL)
    tools = ctx.get("tools") if isinstance(ctx.get("tools"), list) else None
    return await llm_chat_call(model, messages, tools=tools)


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

    raw = await llm_chat_call(
        settings.DEFAULT_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    )
    text = raw["content"].strip().strip('"').lower()
    return {"intent": text, "raw": raw["content"].strip()}
