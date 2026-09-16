"""
RAG 演示节点 — 文档入库与知识库检索。
"""


import asyncio
import contextvars
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.registry.types import func
from app.services import knowledge
from app.utils import files


class RagLoadOutput(BaseModel):
    doc_name: str = Field(description="文档名称")
    text: str = Field(description="文档正文")


class RagEmbedItem(BaseModel):
    chunk_id: str = Field(description="块标识")
    text: str = Field(description="块文本")
    vector: list[float] = Field(description="浮点向量")


class RagRetrieveItem(BaseModel):
    source: str = Field(description="片段来源标识")
    text: str = Field(description="片段正文")


class RagEmbedOutput(BaseModel):
    result: list[RagEmbedItem] = Field(description="向量化结果列表")


class RagRetrieveOutput(BaseModel):
    result: list[RagRetrieveItem] = Field(description="检索结果列表")


class RagUpsertOutput(BaseModel):
    result: str = Field(description="成功写库的向量数量统计")


class RagChunkOutput(BaseModel):
    result: list[str] = Field(description="文本段列表")


class StringResultOutput(BaseModel):
    result: str = Field(description="执行结果")


class RagLoadParams(BaseModel):
    document: dict = Field(description="上传文档")


class RagChunkParams(BaseModel):
    text: str = Field(description="文档正文")


class RagEmbedParams(BaseModel):
    doc_name: str = Field(description="文档名称")
    chunks: list = Field(description="文本段列表")


class RagUpsertParams(BaseModel):
    doc_name: str = Field(default="", description="文档名称")
    embeds: list | None = Field(default=None, description="向量列表")


class RagRetrieveParams(BaseModel):
    prompt: str = Field(default="", description="检索关键词")


class IngestKbDocumentParams(BaseModel):
    upload_id: str = Field(description="上传文件的 ID")
    filename: str = Field(description="文件名（含扩展名）")


class SearchKnowledgeBaseParams(BaseModel):
    query: str = Field(description="检索关键词或自然语言问题")


logger = logging.getLogger(__name__)


@func(
    label="加载文档",
    description="读取上传文档内容",
    metadata={"group": "RAG", "order": 10},

)
async def rag_load(params: RagLoadParams) -> RagLoadOutput:
    """从上传目录读取 params 声明的 document 文件"""
    if not isinstance(params.document, dict):
        raise ValueError("缺少上传文档：document 必须是 {id, filename} 字段")
    upload_id = params.document.get("id")
    if not isinstance(upload_id, str) or not upload_id.strip():
        raise ValueError("上传文档缺少文件id字段：document.id")
    text = files.read_upload(upload_id)  # 路径穿越/扩展名非法/文件缺失 → 中文 ValueError
    if not text.strip():
        raise ValueError("上传文档正文为空：文件内容为空白文本")
    filename = str(params.document.get("filename") or "上传文档")
    stem = Path(filename).stem or "document"
    return RagLoadOutput(doc_name=stem, text=text)


@func(
    label="切块",
    description="按空行把正文切成语义段",
    metadata={"group": "RAG", "order": 20},

)
async def rag_chunk(params: RagChunkParams) -> RagChunkOutput:
    if not isinstance(params.text, str) or not params.text.strip():
        raise ValueError("缺少文档正文字段text")
    return RagChunkOutput(result=[p.strip() for p in params.text.split("\r\n\r\n") if p.strip()])


@func(
    label="向量化",
    description="为每个 chunk 生成向量",
    metadata={"group": "RAG", "order": 30},

)
async def rag_embed(params: RagEmbedParams) -> RagEmbedOutput:
    if not isinstance(params.doc_name, str) or not params.doc_name.strip():
        raise ValueError("缺少文档名称字段doc_name")
    if not isinstance(params.chunks, list):
        raise ValueError("缺少切块信息字段chunks")
    return RagEmbedOutput(result=[
        RagEmbedItem(
            chunk_id=f"{params.doc_name}-c{i}",
            text=chunk,
            vector=[float(sum(ord(c) * (d + 1) for c in chunk) % 997) for d in range(8)],
        )
        for i, chunk in enumerate(params.chunks)
    ])


@func(
    label="写入向量库",
    description="批量写入向量信息",
    metadata={"group": "RAG", "order": 40},

)
async def rag_upsert(params: RagUpsertParams) -> RagUpsertOutput:
    await asyncio.sleep(0.05)
    if not isinstance(params.doc_name, str) or not params.doc_name.strip():
        raise ValueError("缺少文档名称字段doc_name")
    if not isinstance(params.embeds, list):
        raise ValueError("缺少向量输出字段embeds")
    return RagUpsertOutput(result=f"upserted {len(params.embeds)} chunks from {params.doc_name}")


# 模拟知识库：与 rag_retrieve 的演示数据同源（真实实现应为向量库检索，见 rag_embed/rag_upsert）
_MOCK_KB: list[dict[str, Any]] = [
    {
        "source": "lore-001#c0",
        "keywords": ("北境", "要塞", "长城", "纪元", "山脉"),
        "text": "北境要塞建于第二纪元，横贯大陆北端的霜脊山脉。",
    },
    {
        "source": "lore-001#c1",
        "keywords": ("堡垒", "兵力", "风哨", "寒鸦", "冬炉", "驻军"),
        "text": "要塞由风哨、寒鸦、冬炉三段堡垒群组成，常驻兵力约八千。",
    },
    {
        "source": "lore-001#c2",
        "keywords": ("长冬", "补给", "存粮", "商路", "防线"),
        "text": "每逢长冬，商路断绝，冬炉堡的存粮要支撑整条防线的补给。",
    },
]


@func(
    label="知识库检索",
    description="关键词查询返回片段",
    metadata={"group": "RAG", "order": 50},

)
async def rag_retrieve(params: RagRetrieveParams) -> RagRetrieveOutput:
    await asyncio.sleep(0.05)
    ranked = sorted(_MOCK_KB, key=lambda c: -sum(str(params.prompt).count(k) for k in c["keywords"]))
    return RagRetrieveOutput(result=[RagRetrieveItem(source=c["source"], text=c["text"]) for c in ranked[:2]])


# ---------------------------------------------------------------------------
# 知识库（Agent 工具）
# ---------------------------------------------------------------------------

_tool_kb_ctx: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "_tool_kb_ctx", default=None
)


def set_tool_kb_id(kb_id: int | None) -> contextvars.Token:
    return _tool_kb_ctx.set(kb_id)


def reset_tool_kb_id(token: contextvars.Token) -> None:
    _tool_kb_ctx.reset(token)


@func(

    description=(
        "将上传的文档导入关联的知识库，切块并向量化，供后续检索使用。"
        "当用户上传文件并希望围绕该文件内容多轮提问时调用。"
    ),
    metadata={"group": "RAG", "order": 60},

)
async def ingest_kb_document(params: IngestKbDocumentParams) -> StringResultOutput:
    kb_id = _tool_kb_ctx.get()
    if kb_id is None:
        return StringResultOutput(result="当前 Agent 未关联知识库，无法导入文档。请先在 Agent 设置中关联知识库。")
    try:
        result = await knowledge.ingest_document(kb_id, params.upload_id, params.filename)
        return StringResultOutput(result=f"文档「{params.filename}」已导入知识库，共 {result['chunk_count']} 个切块。")
    except Exception as exc:
        return StringResultOutput(result=f"文档导入失败：{exc}")


@func(

    description=(
        "从关联的知识库中检索与问题最相关的文档片段。"
        "当用户提问需要参考已入库文档时使用此工具。"
    ),
    metadata={"group": "RAG", "order": 70},

)
async def search_knowledge_base(params: SearchKnowledgeBaseParams) -> StringResultOutput:
    kb_id = _tool_kb_ctx.get()
    if kb_id is None:
        return StringResultOutput(result="当前 Agent 未关联知识库。请先在 Agent 设置中关联知识库。")

    results = await knowledge.search_chunks(kb_id, params.query, top_k=5)
    if not results:
        return StringResultOutput(result="知识库中没有已入库的文档，请先上传并导入文档。")

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] 来源: {r['filename']}（相似度: {r['similarity']}）\n{r['content']}")
    return StringResultOutput(result="\n\n".join(lines))
