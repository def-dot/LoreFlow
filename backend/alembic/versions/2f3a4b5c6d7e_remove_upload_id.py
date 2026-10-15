"""remove upload_id from documents

Revision ID: 2f3a4b5c6d7e
Revises: cce6ddb98c67
Create Date: 2026-10-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2f3a4b5c6d7e'
down_revision: Union[str, None] = '55f844329b0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('documents', 'upload_id')


def downgrade() -> None:
    op.add_column('documents', sa.Column('upload_id', sa.String(length=200), nullable=False, server_default=''))