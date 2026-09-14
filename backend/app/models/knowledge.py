"""知识库模型 — KnowledgeBase、Document、Chunk。"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel

from app.core.config import settings


class KnowledgeBaseRecord(SQLModel, table=True):
    __tablename__ = "knowledge_bases"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200)
    description: str = Field(default="", sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.now)


class DocumentRecord(SQLModel, table=True):
    __tablename__ = "documents"

    id: int | None = Field(default=None, primary_key=True)
    kb_id: int = Field(index=True, foreign_key="knowledge_bases.id")
    filename: str = Field(max_length=500)
    upload_id: str = Field(max_length=200)
    status: str = Field(default="processing", max_length=20)
    chunk_count: int = 0
    error: str | None = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=datetime.now)


class ChunkRecord(SQLModel, table=True):
    __tablename__ = "chunks"

    id: int | None = Field(default=None, primary_key=True)
    document_id: int = Field(index=True, foreign_key="documents.id")
    content: str = Field(sa_column=Column(Text))
    embedding: list[float] | None = Field(
        default=None,
        sa_column=Column(Vector(settings.EMBEDDING_DIMENSION)),
    )
    chunk_index: int = 0
    created_at: datetime = Field(default_factory=datetime.now)
