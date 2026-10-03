"""rename content to enriched_content

Revision ID: 74b2c518b1b2
Revises: b7e4f2a1c3d5
Create Date: 2026-10-03 13:16:10.697034

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = '74b2c518b1b2'
down_revision: Union[str, None] = 'b7e4f2a1c3d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 把 content 数据迁移到 enriched_content（如果 enriched_content 为空）
    op.execute("UPDATE chunks SET enriched_content = content WHERE enriched_content IS NULL")
    op.drop_column('chunks', 'content')


def downgrade() -> None:
    op.add_column('chunks', sa.Column('content', sa.TEXT(), autoincrement=False, nullable=True))
    op.execute("UPDATE chunks SET content = enriched_content")
