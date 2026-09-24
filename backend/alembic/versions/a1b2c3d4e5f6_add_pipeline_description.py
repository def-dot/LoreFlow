"""add pipelines.description

Revision ID: a1b2c3d4e5f6
Revises: 3b8d02789918
Create Date: 2026-09-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '3b8d02789918'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('pipelines', sa.Column('description', sa.String(), server_default='', nullable=False))


def downgrade() -> None:
    op.drop_column('pipelines', 'description')
