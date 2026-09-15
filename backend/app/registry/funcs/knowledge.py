"""知识库 Agent 工具 — 文档入库与检索。"""

from __future__ import annotations

import contextvars
import logging

from app.registry.types import func
from app.services import knowledge

logger = logging.getLogger(__name__)

_tool_kb_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_tool_kb_ctx", default=None
)


def set_tool_kb_id(kb_id: int | None) -> contextvars.Token:
    return _tool_kb_ctx.set(kb_id)


def reset_tool_kb_id(token: contextvars.Token) -> None:
    _tool_kb_ctx.reset(token)


@func(
    name="ingest_kb_document",
    description=(
        "将上传的文档导入关联的知识库，切块并向量化，供后续检索使用。"
        "当用户上传文件并希望围绕该文件内容多轮提问时调用。"
    ),
    params={
        "upload_id": "上传文件的 ID",
        "filename": "文件名（含扩展名）",
    },
)
async def ingest_kb_document_tool(upload_id: str, filename: str) -> str:
    kb_id = _tool_kb_ctx.get()
    if kb_id is None:
        return "当前 Agent 未关联知识库，无法导入文档。请先在 Agent 设置中关联知识库。"
    try:
        result = await knowledge.ingest_document(kb_id, upload_id, filename)
        return f"文档「{filename}」已导入知识库，共 {result['chunk_count']} 个切块。"
    except Exception as exc:
        return f"文档导入失败：{exc}"


@func(
    name="search_knowledge_base",
    description=(
        "从关联的知识库中检索与问题最相关的文档片段。"
        "当用户提问需要参考已入库文档时使用此工具。"
    ),
    params={"query": "检索关键词或自然语言问题"},
)
async def search_knowledge_base_tool(query: str) -> str:
    kb_id = _tool_kb_ctx.get()
    if kb_id is None:
        return "当前 Agent 未关联知识库。请先在 Agent 设置中关联知识库。"

    results = await knowledge.search_chunks(kb_id, query, top_k=5)
    if not results:
        return "知识库中没有已入库的文档，请先上传并导入文档。"

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] 来源: {r['filename']}（相似度: {r['similarity']}）\n{r['content']}")
    return "\n\n".join(lines)
