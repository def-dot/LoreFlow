"""Database models — run records."""

from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class RunRecord(SQLModel, table=True):
    """One persisted run snapshot (nodes stored as JSON)."""

    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)  # 自增
    name: str = ""  # yaml 里的 name 字段（如 content_pipeline）
    config_file: str = ""  # yaml 文件名（如 pipeline.yaml）
    mermaid: str = ""  # pipeline 图源码（创建 run 时快照进记录）
    created_at: str | None = None
    finished_at: str | None = None
    status: str = "pending"  # pending/running/reviewing/completed/failed/cancelled
    error: str | None = None
    nodes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
