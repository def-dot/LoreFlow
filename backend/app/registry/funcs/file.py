"""
文件节点 — 读取上传文档内容（通用，不依赖 RAG）。
"""

from pathlib import Path

from pydantic import BaseModel, Field

from app.registry.types import func
from app.utils import files


class DocumentRef(BaseModel):
    stored_name: str = Field(description="UUID 存储文件名", min_length=1)
    filename: str = Field(description="原始文件名", min_length=1)


class ReadDocumentParams(BaseModel):
    document: DocumentRef = Field(description="上传文档")


class ReadDocumentOutput(BaseModel):
    doc_name: str = Field(description="文档名称")
    text: str = Field(description="文档正文")


@func(
    label="读取文档",
    description="读取上传文档内容",
    metadata={"group": "文件", "order": 10},
)
async def read_document(params: ReadDocumentParams) -> ReadDocumentOutput:
    """从上传目录读取 params 声明的 document 文件"""
    text = files.read_upload(params.document.stored_name)
    if not text.strip():
        raise ValueError("上传文档正文为空：文件内容为空白文本")
    stem = Path(params.document.filename).stem or "document"
    return ReadDocumentOutput(doc_name=stem, text=text)
