"""Database models — human-review decisions."""

from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel


class ReviewDecision(SQLModel, table=True):
    """一条人工审批决策——审批器挂起检查/续跑时从这张表认领决策。

    行保留（consumed_at 标记消费时刻）作为审计痕迹；未消费决策对
    (run_id, node_name) 最多一条（部分唯一索引），重复提交覆盖旧值，
    避免陈旧决策被该节点的下一次到达误消费。
    """

    __tablename__ = "review_decisions"

    __table_args__ = (
        Index(
            "uq_review_decisions_pending",
            "run_id",
            "node_name",
            unique=True,
            postgresql_where=text("consumed_at IS NULL"),
            sqlite_where=text("consumed_at IS NULL"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    run_id: int
    node_name: str
    approve: bool
    reason: str | None = None
    created_at: str | None = None
    consumed_at: str | None = None
