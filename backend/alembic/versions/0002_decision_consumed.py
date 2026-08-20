"""decision consumed_at + partial unique index

决策从"取走即删"改为"认领后保留"：consumed_at 标记消费时刻作为审计
痕迹；部分唯一索引保证未消费决策对 (run_id, node_name) 最多一条
（重复提交覆盖旧值，杜绝陈旧决策串代）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("review_decisions", sa.Column("consumed_at", sa.String(), nullable=True))
    # 旧库可能有重复提交残留的未消费决策：每对 (run_id, node_name) 只留最新一条
    op.execute(
        sa.text(
            """
            DELETE FROM review_decisions AS d
            WHERE d.consumed_at IS NULL
              AND EXISTS (
                SELECT 1 FROM review_decisions AS o
                WHERE o.run_id = d.run_id AND o.node_name = d.node_name
                  AND o.consumed_at IS NULL AND o.id > d.id
              )
            """
        )
    )
    op.create_index(
        "uq_review_decisions_pending",
        "review_decisions",
        ["run_id", "node_name"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
        sqlite_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_review_decisions_pending",
        table_name="review_decisions",
        postgresql_where=sa.text("consumed_at IS NULL"),
        sqlite_where=sa.text("consumed_at IS NULL"),
    )
    op.drop_column("review_decisions", "consumed_at")
