"""知识库模型 — Document、Chunk、Tag。"""

from datetime import datetime
from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY, INTEGER, TSVECTOR
from sqlmodel import Field, SQLModel

from app.core.config import settings


# ---------- 枚举 ----------


class DocumentStatus(StrEnum):
    """文档处理状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------- 文档记录 ----------


class DocumentRecord(SQLModel, table=True):
    __tablename__ = "documents"

    id: int | None = Field(default=None, primary_key=True)
    filename: str = Field(max_length=500)
    upload_id: str = Field(max_length=200)
    file_path: str | None = Field(default=None, max_length=500)
    file_size: int | None = Field(default=None)
    file_ext: str | None = Field(default=None, max_length=20)
    status: str = Field(default=DocumentStatus.PENDING, max_length=20)
    chunk_count: int = 0
    error: str | None = Field(default=None, sa_column=Column(Text))
    content_hash: str | None = Field(default=None, max_length=64, index=True)
    parse_duration_ms: int | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime | None = Field(default=None)


# ---------- 切片 ----------


class ChunkRecord(SQLModel, table=True):
    __tablename__ = "chunks"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True, foreign_key="documents.id")
    file_name: str | None = Field(default=None, max_length=255)
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(settings.EMBEDDING_DIMENSION)),
    )
    page_numbers: list[int] | None = Field(default=None, sa_type=ARRAY(INTEGER))  # type: ignore[call-overload]
    heading_context: str | None = Field(default=None, sa_column=Column(Text))
    raw_content: str | None = Field(default=None, sa_column=Column(Text))
    enriched_content: str | None = Field(default=None, sa_column=Column(Text))
    tsv_content: str | None = Field(default=None, sa_type=TSVECTOR)

    __table_args__ = (
        Index(
            "ix_chunks_embedding_hnsw", "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_chunks_tsv_content", "tsv_content",
            postgresql_using="gin",
        ),
    )


# ---------- 标签 ----------


class TagRecord(SQLModel, table=True):
    __tablename__ = "tags"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=50, unique=True)
    created_at: datetime = Field(default_factory=datetime.now)


class DocumentTagRecord(SQLModel, table=True):
    __tablename__ = "document_tags"

    document_id: int = Field(foreign_key="documents.id", primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)