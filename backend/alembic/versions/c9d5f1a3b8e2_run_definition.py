"""runs pin the workflow definition they were created with

Revision ID: c9d5f1a3b8e2
Revises: b7c4e2f9a3d1
Create Date: 2026-08-25 00:00:00.000000

定义快照钉住：创建 run 时把 YAML 原文存进 runs.definition，恢复
（审批续跑/重启恢复）按钉住版本构建 DAG，而非磁盘上的当前文件。
挂起期间工作流被改（改函数体/改名/删 param）不再漂移进在途 run；
文件与快照不一致时仅记 warning。存量旧行 definition 为 NULL，
恢复回退读当前文件（历史行为不变）。
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c9d5f1a3b8e2'
down_revision: Union[str, None] = 'b7c4e2f9a3d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('runs', sa.Column('definition', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('runs', 'definition')
