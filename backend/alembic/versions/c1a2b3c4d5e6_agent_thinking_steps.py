"""add reasoning_content and execution_steps to messages

Revision ID: c1a2b3c4d5e6
Revises: bf69f2ea95c5
Create Date: 2026-09-15 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1a2b3c4d5e6'
down_revision: Union[str, None] = 'bf69f2ea95c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('reasoning_content', sa.Text(), nullable=True))
    op.add_column('messages', sa.Column('execution_steps', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('messages', 'execution_steps')
    op.drop_column('messages', 'reasoning_content')
