"""remove content_hash from documents

Revision ID: 3c4d5e6f7a8b
Revises: 2f3a4b5c6d7e
Create Date: 2026-10-15 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '3c4d5e6f7a8b'
down_revision: Union[str, None] = '2f3a4b5c6d7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_documents_content_hash', table_name='documents')
    op.drop_column('documents', 'content_hash')


def downgrade() -> None:
    op.add_column('documents', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_documents_content_hash', 'documents', ['content_hash'])