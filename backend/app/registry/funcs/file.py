"""
文件节点 — 读取上传文档内容（通用，不依赖 RAG）。
"""

import asyncio
from pathlib import Path

from docling.document_converter import DocumentConverter
from pydantic import BaseModel, Field

from app.registry.types import node_and_tool

_converter = DocumentConverter()


class ReadDocumentInput(BaseModel):
    document: str = Field(description="文档相对路径，如 uploads/abc.txt", min_length=1)


class ReadDocumentOutput(BaseModel):
    text: str = Field(description="文档正文（Markdown 格式）")


@node_and_tool(
    label="读取文档",
    description="读取上传文档内容（支持 txt/md/pdf/docx/xlsx）",
    metadata={"group": "基础", "order": 12},
)
async def read_document(params: ReadDocumentInput) -> ReadDocumentOutput:
    """从相对路径读取文档，返回 Markdown 格式文本"""
    path = Path(params.document)
    if ".." in params.document:
        raise ValueError("无效的文件路径")
    if not path.is_file():
        raise ValueError(f"文件不存在：{params.document}")
    result = await asyncio.to_thread(_converter.convert, str(path))
    text = result.document.export_to_markdown()
    return ReadDocumentOutput(text=text)