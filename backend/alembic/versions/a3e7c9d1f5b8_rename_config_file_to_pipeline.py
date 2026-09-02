"""config_file → pipeline（字段语义从文件名改为工作流名称）

Revision ID: a3e7c9d1f5b8
Revises: b4e8f2a06d95
Create Date: 2026-09-02

RunRecord.config_file 存的是 YAML 文件名（如 pipeline.yaml），
现改为 pipeline 存工作流名称（YAML 的 name 字段）。
存量数据 migration：从 definition 快照解析 name 回填，无法解析的
退而用原 config_file 值（文件名）。
"""

from typing import Sequence, Union

import yaml
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3e7c9d1f5b8'
down_revision: Union[str, None] = 'b4e8f2a06d95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 新增 pipeline 列
    op.add_column('runs', sa.Column('pipeline', sa.String(), server_default=''))

    # 2. 从 definition 快照回填存量数据
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, config_file, definition FROM runs")).fetchall()
    for row in rows:
        name = ''
        if row.definition:
            try:
                cfg = yaml.safe_load(row.definition)
                if isinstance(cfg, dict):
                    name = cfg.get('name', '')
            except Exception:
                pass
        if not name:
            name = row.config_file or ''
        conn.execute(
            sa.text("UPDATE runs SET pipeline = :name WHERE id = :id"),
            {'name': name, 'id': row.id},
        )

    # 3. 删除旧列
    op.drop_column('runs', 'config_file')


def downgrade() -> None:
    op.add_column('runs', sa.Column('config_file', sa.String(), server_default=''))
    op.execute(sa.text("UPDATE runs SET config_file = pipeline"))
    op.drop_column('runs', 'pipeline')
