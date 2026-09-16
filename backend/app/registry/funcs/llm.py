"""
LLM 节点 — llm_chat / llm_classify，调用 app.services.llm 的公共接口。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.llm import llm_chat_call
from app.registry.types import func


class LLMChatOutput(BaseModel):
    content: str = Field(description="LLM 回复文本")
    tool_calls: list = Field(default_factory=list, description="模型请求的工具调用列表（未传 tools 时为空）")


class LLMClassifyOutput(BaseModel):
    intent: str = Field(description="分类标签")
    raw: str = Field(description="原始回复文本")


@func(
    label="LLM 对话",
    metadata={"group": "LLM", "order": 20},
    description="调用 LLM 模型生成回答，支持 Ollama / MiMo 等 OpenAI 兼容后端",
    params={
        "prompt": "用户提示词",
        "system": "系统提示词",
        "context": "上下文",
        "model": "模型名",
        "tools": "工具定义列表（OpenAI function calling 格式）",
    },
    output_model=LLMChatOutput,
)
async def llm_chat(
    prompt: str,
    system: str | None = None,
    context: str | None = None,
    model: str | None = None,
    tools: list | None = None,
) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML inputs 声明为必填，创建运行时提供）")
    messages: list[dict[str, str]] = []
    if isinstance(context, str) and context.strip():
        prompt = f"参考资料：\n{context}\n\n用户问题：{prompt}"
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return await llm_chat_call(model, messages, tools=tools if isinstance(tools, list) else None)


@func(
    label="意图识别",
    description="通用意图分类器",
    metadata={"group": "LLM", "order": 30},
    params={
        "prompt": "待分类文本",
        "model": "模型名（provider:model 格式）",
        "classify_system": "分类系统提示词",
        "classify_labels": "可选标签列表",
    },
    output_model=LLMClassifyOutput,
)
async def llm_classify(
    prompt: str,
    model: str | None = None,
    classify_system: str | None = None,
    classify_labels: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串")

    if not isinstance(classify_system, str) or not classify_system.strip():
        classify_system = (
            "你是意图分类器，只允许输出以下标签之一，"
            "禁止输出其他任何内容（包括标点、解释或其他语言）：\n"
            "chat —— 问候、闲聊、创作、翻译等无需查询资料的请求\n"
            "rag —— 需要查询知识库/设定资料才能回答的问题\n"
            "search —— 需要联网搜索最新信息才能回答（时事新闻、实时数据等）\n"
            "human —— 需要转人工客服"
        )

    if not isinstance(classify_labels, list) or not classify_labels:
        classify_labels = ["chat", "rag", "search", "human"]

    raw = await llm_chat_call(
        model,
        [{"role": "system", "content": classify_system}, {"role": "user", "content": prompt}],
    )
    text = raw["content"].strip().strip('"').lower()
    return {"intent": text, "raw": raw["content"].strip()}
