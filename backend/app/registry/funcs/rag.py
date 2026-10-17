"""
RAG 节点 — 知识库检索。

知识库管理（创建/导入/删除）由 knowledge 服务模块负责，
管线和 Agent 只需要检索节点。
"""

from pydantic import BaseModel, Field

from app.registry.types import node_and_tool
from app.services import knowledge


class ChunkResult(BaseModel):
    chunk_id: int = Field(description="切片 ID")
    content: str = Field(description="切片内容")
    file_path: str = Field(description="来源文件路径")
    similarity: float = Field(description="相似度分数")


class SearchKnowledgeOutput(BaseModel):
    sources: list[ChunkResult] = Field(description="检索结果列表（结构化，含 chunk_id / 内容 / 来源 / 相似度）")


class SearchKnowledgeParams(BaseModel):
    queries: str | list[str] = Field(description="检索关键词。简单问题传单个字符串；复杂问题（对比、多实体、多维度）拆分为多个子问题传列表")
    tags: list[str] | None = Field(default=None, description="按标签名称筛选，为空则检索全部")


@node_and_tool(
    label="检索知识库",
    description=(
        "混合检索知识库：向量 + BM25 双路召回，RRF 融合去重，Reranker 精排后返回 Top-K。"
        "支持多查询并发检索，按标签过滤。"
    ),
    metadata={"group": "基础", "order": 30},
)
async def retrieve_knowledge(params: SearchKnowledgeParams) -> SearchKnowledgeOutput:
    queries = [params.queries] if isinstance(params.queries, str) else params.queries
    results = await knowledge.search_multi(
        queries, tags=params.tags or None, top_k=5,
    )
    return SearchKnowledgeOutput(sources=results)