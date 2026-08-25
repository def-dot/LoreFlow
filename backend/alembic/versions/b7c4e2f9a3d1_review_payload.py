"""review decisions carry the payload shown to the reviewer

Revision ID: b7c4e2f9a3d1
Revises: e5b7d2f4a9c1
Create Date: 2026-08-25 00:00:00.000000

决策行自包含审计：审批时把挂起快照里的审核视图（review_decisions.payload，
JSON）随决策一并入库。run record 的节点 entry 在完成后被 output 覆盖，
决策表是"认领后保留"的审计链——不 join run record 即可还原审核者当时
看到的 payload 与其决策（approve/reason/edits）的对应关系。
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c4e2f9a3d1'
down_revision: Union[str, None] = 'e5b7d2f4a9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('review_decisions', sa.Column('payload', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('review_decisions', 'payload')
