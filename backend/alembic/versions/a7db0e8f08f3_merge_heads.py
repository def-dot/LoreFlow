"""merge heads

Revision ID: a7db0e8f08f3
Revises: 57eb121ff485, 7c8d9e0f1a2b
Create Date: 2026-10-16 12:18:32.159338

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = 'a7db0e8f08f3'
down_revision: Union[str, None] = ('57eb121ff485', '7c8d9e0f1a2b')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
