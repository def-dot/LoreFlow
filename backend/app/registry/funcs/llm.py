"""
LLM 节点 — llm_chat / llm_classify，调用 app.services.llm 的公共接口。
"""

from typing import Any

from pydantic import BaseModel, Field

from app.services.llm import llm_chat_call
from app.registry.types import func


_CLASSIFY_SYSTEM = (
    "你是意图分类器，只允许输出以下标签之一，"
    "禁止输出其他任何内容（包括标点、解释或其他语言）：\n"
    "chat —— 问候、闲聊、创作、翻译等无需查询资料的请求\n"
    "rag —— 需要查询知识库/设定资料才能回答的问题\n"
    "search —— 需要联网搜索最新信息才能回答（时事新闻、实时数据等）\n"
    "human —— 需要转人工客服"
)

_CLASSIFY_LABELS = ["chat", "rag", "search", "human"]


class LLMChatOutput(BaseModel):
    content: str = Field(description="LLM 回复文本")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, description="模型请求的工具调用列表")


class LLMClassifyOutput(BaseModel):
    intent: str = Field(description="分类标签")
    raw: str = Field(description="原始回复文本")


class LLMChatParams(BaseModel):
    prompt: str = Field(description="用户提示词", min_length=1)
    system: str | None = Field(default=None, description="系统提示词")
    context: str | None = Field(default=None, description="上下文")
    model: str | None = Field(default=None, description="模型名")
    tools: list[str] | None = Field(default=None, description="工具定义列表（OpenAI function calling 格式）")


class LLMClassifyParams(BaseModel):
    prompt: str = Field(description="待分类文本", min_length=1)
    model: str | None = Field(default=None, description="模型名")
    classify_system: str = Field(default=_CLASSIFY_SYSTEM, description="分类系统提示词")
    classify_labels: list[str] = Field(default=_CLASSIFY_LABELS, description="可选标签列表")


@func(
    label="LLM 对话",
    metadata={"group": "LLM", "order": 20},
    description="调用 LLM 模型生成回答",
)
async def llm_chat(params: LLMChatParams) -> LLMChatOutput:
    messages: list[dict[str, str]] = []
    prompt = params.prompt
    if params.context and params.context.strip():
        prompt = f"参考资料：\n{params.context}\n\n用户问题：{params.prompt}"
    if params.system and params.system.strip():
        messages.append({"role": "system", "content": params.system})
    messages.append({"role": "user", "content": prompt})
    raw = await llm_chat_call(params.model, messages, tools=params.tools)
    return LLMChatOutput(content=raw["content"], tool_calls=raw["tool_calls"])


@func(
    label="意图识别",
    description="通用意图分类器",
    metadata={"group": "LLM", "order": 30},
)
async def llm_classify(params: LLMClassifyParams) -> LLMClassifyOutput:
    raw = await llm_chat_call(
        params.model,
        [{"role": "system", "content": params.classify_system}, {"role": "user", "content": params.prompt}],
    )
    text = raw["content"].strip().strip('"').lower()
    return LLMClassifyOutput(intent=text, raw=raw["content"].strip())
