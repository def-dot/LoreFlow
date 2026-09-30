"""知识库相关 Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class DocumentListItem(BaseModel):
    id: int
    filename: str
    status: str
    chunk_count: int
    error: str | None = None
    parse_duration_ms: int | None = None
    created_at: str | None = None
    kb_id: int
    kb_name: str = ""


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    total: int


class ChunkItem(BaseModel):
    id: int
    document_id: int
    file_name: str | None = None
    page_numbers: list[int] = []
    heading_context: str = ""
    raw_content: str = ""


class ChunkListResponse(BaseModel):
    items: list[ChunkItem]
    total: int


class StatusCounts(BaseModel):
    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0