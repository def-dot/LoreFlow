"""
LLM 节点 — llm_chat / llm_classify / llm_rewrite_query，调用 app.services.llm 的公共接口。
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.services.llm import llm_chat_call
from app.registry.types import node

logger = logging.getLogger(__name__)


class LLMChatOutput(BaseModel):
    content: str = Field(description="LLM 回复文本")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, description="模型请求的工具调用列表")


class LLMClassifyOutput(BaseModel):
    intent: str = Field(description="分类标签")


class LLMChatParams(BaseModel):
    prompt: str = Field(description="用户提示词", min_length=1)
    system: str | None = Field(default=None, description="系统提示词")
    context: str | list[Any] | None = Field(default=None, description="上下文")
    model_key: str | None = Field(default=None, description="模型引用（provider_name/model_name 格式，None 则用默认）")
    tools: list[str] | None = Field(default=None, description="工具定义列表（OpenAI function calling 格式）")


class LLMClassifyParams(BaseModel):
    prompt: str = Field(description="待分类文本", min_length=1)
    model_key: str | None = Field(default=None, description="模型引用（provider_name/model_name 格式，None 则用默认）")
    classify_labels: list[str] = Field(description="可选标签列表", min_length=1)


@node(
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
    raw = await llm_chat_call(params.model_key, messages, tools=params.tools)
    return LLMChatOutput(content=raw["content"], tool_calls=raw["tool_calls"])


@node(
    label="文本分类",
    description="通用文本分类器，从指定标签中选出最匹配的一个",
    metadata={"group": "LLM", "order": 30},
)
async def llm_classify(params: LLMClassifyParams) -> LLMClassifyOutput:
    system = f"你是文本分类器，从以下标签中选择最匹配的一个：{', '.join(params.classify_labels)}。只输出所选标签。"
    # 标签是运行时参数：动态建模生成带 enum 的 schema（extra=forbid → additionalProperties: false）
    result_model = create_model(
        "ClassifyResult",
        __config__=ConfigDict(extra="forbid"),
        intent=(Literal[*params.classify_labels], Field(description="分类标签")),
    )
    raw = await llm_chat_call(
        params.model_key,
        [{"role": "system", "content": system}, {"role": "user", "content": params.prompt}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "classify_result",
                "strict": True,
                "schema": result_model.model_json_schema(),
            },
        },
    )
    result = result_model.model_validate_json(raw["content"])
    return LLMClassifyOutput(intent=result.intent)


# ---------------------------------------------------------------------------
# 查询改写 + 拆分
# ---------------------------------------------------------------------------

_REWRITE_SYSTEM = """\
你是检索查询优化器。对用户问题做两步处理：
1. 改写：将问题改写为更适合向量检索的短句（补全省略、消除指代、展开缩写）
2. 拆分：将改写后的问题拆分为多个独立的检索子问题

输出 JSON 格式：
- search_query：改写后的主查询，用于最终生成回答
- sub_queries：拆分后的检索子问题列表，用于并发检索

规则：
- 简单问题：search_query 和 sub_queries 相同（一个元素）
- 复杂问题（对比、多实体、多维度）：sub_queries 拆分为 2-4 个独立子问题
- 每个子问题应改写为适合向量检索的短句形式"""


class RewriteQueryParams(BaseModel):
    query: str = Field(description="用户问题", min_length=1)
    history: str | None = Field(default=None, description="对话历史（JSON 字符串）")
    model_key: str | None = Field(default=None, description="模型引用（provider_name/model_name 格式，None 则用默认）")


class RewriteQueryOutput(BaseModel):
    # extra="forbid" 使 schema 带 additionalProperties: false（strict response_format 的硬性要求）
    model_config = ConfigDict(extra="forbid")

    search_query: str = Field(description="改写后的主查询")
    sub_queries: list[str] = Field(description="拆分后的子问题列表")


@node(
    label="查询改写",
    description="改写用户问题并拆分为多个检索子问题",
    metadata={"group": "LLM", "order": 35},
)
async def llm_rewrite_query(params: RewriteQueryParams) -> RewriteQueryOutput:
    user_content = params.query
    if params.history:
        user_content = f"对话历史：\n{params.history}\n\n当前问题：{params.query}"

    raw = await llm_chat_call(
        params.model_key,
        [
            {"role": "system", "content": _REWRITE_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "rewrite_query",
                "strict": True,
                "schema": RewriteQueryOutput.model_json_schema(),
            },
        },
    )

    try:
        return RewriteQueryOutput.model_validate_json(raw["content"])
    except Exception:
        logger.warning("[llm_rewrite_query] 解析失败，使用原问题: %s", raw["content"][:200])
        return RewriteQueryOutput(search_query=params.query, sub_queries=[params.query])
