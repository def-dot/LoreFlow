"""review decisions carry reviewer edits

Revision ID: e5b7d2f4a9c1
Revises: a7d41e90c2f5
Create Date: 2026-08-24 00:00:00.000000

人工审核支持"改了再通过"：审核者在审批卡片上对字符串字段的修订随
决策入库（review_decisions.edits，JSON）。引擎只把修订应用进审核
节点的输出 payload，上游节点输出不动——审计链保持完整。
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5b7d2f4a9c1'
down_revision: Union[str, None] = 'a7d41e90c2f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('review_decisions', sa.Column('edits', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('review_decisions', 'edits')
