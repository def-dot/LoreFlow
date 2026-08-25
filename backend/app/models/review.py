"""Database models — human-review decisions."""

from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


class ReviewDecision(SQLModel, table=True):
    """一条人工审批决策——审批器挂起检查/续跑时从这张表认领决策。
    """

    __tablename__ = "review_decisions"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int
    node_name: str
    approve: bool
    reason: str | None = None
    edits: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: str | None = None
    consumed_at: str | None = None
