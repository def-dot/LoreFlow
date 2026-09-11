"""drop max_iterations from agents

Revision ID: c3d4e5f6a7b8
Revises: f1a2b3c4d5e6
Create Date: 2026-09-21 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('agents', 'max_iterations')


def downgrade() -> None:
    op.add_column('agents', sa.Column('max_iterations', sa.Integer(), nullable=True))
