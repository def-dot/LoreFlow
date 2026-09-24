"""uploads: id (UUID string) → id (int) + stored_name

Revision ID: e5f6a7b8c9d0
Revises: d21b00b8f6cb
Create Date: 2026-09-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd21b00b8f6cb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 添加 stored_name 列（暂 nullable）
    op.add_column('uploads', sa.Column('stored_name', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    # 2. 把旧 id（UUID 文件名）复制到 stored_name
    op.execute("UPDATE uploads SET stored_name = id")
    # 3. 删除旧主键
    op.drop_constraint('uploads_pkey', 'uploads', type_='primary')
    # 4. 把旧 id 列改名为 stored_name 已完成，删掉旧列
    op.drop_column('uploads', 'id')
    # 5. 添加自增 id 作为新主键
    op.add_column('uploads', sa.Column('id', sa.Integer(), autoincrement=True, nullable=False))
    op.create_primary_key('uploads_pkey', 'uploads', ['id'])
    # 6. stored_name 设为 NOT NULL + UNIQUE
    op.alter_column('uploads', 'stored_name', nullable=False)
    op.create_unique_constraint('uq_uploads_stored_name', 'uploads', ['stored_name'])


def downgrade() -> None:
    op.drop_constraint('uq_uploads_stored_name', 'uploads', type_='unique')
    op.drop_constraint('uploads_pkey', 'uploads', type_='primary')
    op.drop_column('uploads', 'id')
    op.add_column('uploads', sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False))
    op.execute("UPDATE uploads SET id = stored_name")
    op.create_primary_key('uploads_pkey', 'uploads', ['id'])
    op.drop_column('uploads', 'stored_name')