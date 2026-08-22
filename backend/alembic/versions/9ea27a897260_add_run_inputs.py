"""add run inputs

Revision ID: 9ea27a897260
Revises: b98b4d88fdec
Create Date: 2026-08-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ea27a897260'
down_revision: Union[str, None] = 'b98b4d88fdec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 运行时输入快照：创建 run 时持久化，审批续跑/重启恢复时回放进共享上下文
    op.add_column('runs', sa.Column('inputs', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'inputs')
