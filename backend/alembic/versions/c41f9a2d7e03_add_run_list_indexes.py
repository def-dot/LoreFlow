"""add run list indexes

Revision ID: c41f9a2d7e03
Revises: 9ea27a897260
Create Date: 2026-08-23 00:00:00.000000

列表接口按 status / config_file 筛选、按 created_at 排序（ISO 字符串，
字典序即时间序），数据量上千后这三列的索引决定查询与 COUNT 的代价。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41f9a2d7e03'
down_revision: Union[str, None] = '9ea27a897260'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_runs_status', 'runs', ['status'])
    op.create_index('ix_runs_config_file', 'runs', ['config_file'])
    op.create_index('ix_runs_created_at', 'runs', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_runs_created_at', table_name='runs')
    op.drop_index('ix_runs_config_file', table_name='runs')
    op.drop_index('ix_runs_status', table_name='runs')
