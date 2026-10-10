"""
RAG 节点 — 知识库检索。

知识库管理（创建/导入/删除）由 knowledge 服务模块负责，
管线和 Agent 只需要检索节点。
"""

from pydantic import BaseModel, Field

from app.registry.types import node
from app.services import knowledge


class ChunkResult(BaseModel):
    chunk_id: int = Field(description="切片 ID")
    content: str = Field(description="切片内容")
    filename: str = Field(description="来源文件名")
    similarity: float = Field(description="相似度分数")


class SearchKnowledgeOutput(BaseModel):
    sources: list[ChunkResult] = Field(description="检索结果列表（结构化）")
    context: str = Field(description="检索结果（格式化文本，用于 LLM 上下文）")


class SearchKnowledgeParams(BaseModel):
    queries: str | list[str] = Field(description="检索关键词，单个字符串或列表")
    tags: list[str] = Field(default_factory=list, description="按标签名称筛选，为空则检索全部")
    doc_ids: list[int] | None = Field(default=None, description="按文档 ID 筛选，用于检索特定上传文件")


@node(
    label="检索知识库",
    description=(
        "从知识库中检索与问题最相关的文档片段。"
        "支持多查询并发检索并自动去重。"
        "可通过 doc_ids 参数限定检索特定文档。"
    ),
    metadata={"group": "基础", "order": 30},
)
async def retrieve_knowledge(params: SearchKnowledgeParams) -> SearchKnowledgeOutput:
    queries = [params.queries] if isinstance(params.queries, str) else params.queries
    results = await knowledge.search_multi(
        queries, tags=params.tags or None, top_k=5, doc_ids=params.doc_ids,
    )
    return SearchKnowledgeOutput(
        sources=results,
        context=knowledge.format_sources(results),
    )