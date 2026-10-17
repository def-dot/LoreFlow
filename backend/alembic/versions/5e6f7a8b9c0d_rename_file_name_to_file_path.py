"""rename file_name to file_path in chunks

Revision ID: 5e6f7a8b9c0d
Revises: a7db0e8f08f3
Create Date: 2026-10-18 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5e6f7a8b9c0d'
down_revision: Union[str, None] = 'a7db0e8f08f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('chunks', 'file_name', new_column_name='file_path')


def downgrade() -> None:
    op.alter_column('chunks', 'file_path', new_column_name='file_name')