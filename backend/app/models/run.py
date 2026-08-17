"""
Database models — run records and human-review decisions.
"""

from typing import Any, Dict, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class RunRecord(SQLModel, table=True):
    """One persisted run snapshot (nodes stored as JSON)."""

    __tablename__ = "runs"

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增
    name: str = ""          # yaml 里的 name 字段（如 content_pipeline）
    config_file: str = ""   # yaml 文件名（如 pipeline.yaml）
    mermaid: str = ""       # pipeline 图源码（创建 run 时快照进记录）
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: str = "pending"  # pending/running/completed/failed/cancelled
    error: Optional[str] = None
    nodes: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class ReviewDecision(SQLModel, table=True):
    """一条人工审批决策——审批器挂起时轮询这张表取决策。"""

    __tablename__ = "review_decisions"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int
    node_name: str
    approve: bool
    reason: Optional[str] = None
    created_at: Optional[str] = None
