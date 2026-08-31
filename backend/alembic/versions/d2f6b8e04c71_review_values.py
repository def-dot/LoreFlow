"""review_decisions.edits → values（审核返回字段终值，替代差量）

Revision ID: d2f6b8e04c71
Revises: c9d5f1a3b8e2
Create Date: 2026-08-30

决策携带的不再是"改动键差量 edits"，而是审核者返回的字段终值 values
（未改字段即原值；改动可由 payload 与 values 对比得出）。列数据形态
同为 JSON 映射，纯改名。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2f6b8e04c71"
down_revision: Union[str, None] = "c9d5f1a3b8e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "review_decisions", "edits", new_column_name="values", existing_type=sa.JSON()
    )


def downgrade() -> None:
    op.alter_column(
        "review_decisions", "values", new_column_name="edits", existing_type=sa.JSON()
    )
