"""
RAG 节点 — 知识库检索。

知识库管理（创建/导入/删除）由 knowledge 服务模块负责，
管线和 Agent 只需要检索节点。
"""

import contextvars
import logging

from pydantic import BaseModel, Field

from app.registry.types import func
from app.services import knowledge


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class SearchKnowledgeItem(BaseModel):
    source: str = Field(description="片段来源（文件名）")
    text: str = Field(description="片段正文")


class SearchKnowledgeOutput(BaseModel):
    result: list[SearchKnowledgeItem] = Field(description="检索结果列表")


class SearchKnowledgeParams(BaseModel):
    query: str = Field(description="检索关键词或自然语言问题", min_length=1)


# ---------------------------------------------------------------------------
# Agent 工具上下文
# ---------------------------------------------------------------------------

_tool_kb_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_tool_kb_ctx", default=None
)


def set_tool_kb_id(kb_id: int | None) -> contextvars.Token:
    return _tool_kb_ctx.set(kb_id)


def reset_tool_kb_id(token: contextvars.Token) -> None:
    _tool_kb_ctx.reset(token)


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


@func(
    label="检索知识库",
    description=(
        "从知识库中检索与问题最相关的文档片段。"
        "当用户提问需要参考已入库文档时使用此工具。"
    ),
    metadata={"group": "基础", "order": 30},
)
async def search_knowledge(params: SearchKnowledgeParams) -> SearchKnowledgeOutput:
    kb_id = _tool_kb_ctx.get()
    results = await knowledge.search_chunks(kb_id, params.query, top_k=5)
    return SearchKnowledgeOutput(result=[
        SearchKnowledgeItem(source=r["filename"], text=r["content"])
        for r in results
    ])

