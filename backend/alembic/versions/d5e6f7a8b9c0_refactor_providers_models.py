"""refactor providers into providers + models + settings

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-10 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = 'd5e6f7a8b9c0'
down_revision: Union[str, None] = 'c4d5e6f7a8b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. providers 表：去掉 models / is_default / default_model
    op.drop_column('providers', 'models')
    op.drop_column('providers', 'is_default')
    op.drop_column('providers', 'default_model')

    # 2. 创建 models 表
    op.create_table('models',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('provider_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('model_type', sa.String(length=20), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['provider_id'], ['providers.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_models_provider_id', 'models', ['provider_id'])

    # 3. 创建 settings 表
    op.create_table('settings',
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('key'),
    )


def downgrade() -> None:
    op.drop_table('settings')
    op.drop_index('ix_models_provider_id', 'models')
    op.drop_table('models')
    op.add_column('providers', sa.Column('default_model', sa.String(), nullable=False, server_default=''))
    op.add_column('providers', sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('providers', sa.Column('models', sa.JSON(), nullable=True))