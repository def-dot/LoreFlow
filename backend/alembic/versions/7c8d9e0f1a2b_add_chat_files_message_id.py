"""add message_id to chat_files

Revision ID: 7c8d9e0f1a2b
Revises: 5a6b7c8d9e0f
Create Date: 2026-10-16 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c8d9e0f1a2b'
down_revision: Union[str, None] = '5a6b7c8d9e0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('chat_files', sa.Column('message_id', sa.Integer(), nullable=True))
    op.create_index('ix_chat_files_message_id', 'chat_files', ['message_id'])


def downgrade() -> None:
    op.drop_index('ix_chat_files_message_id', table_name='chat_files')
    op.drop_column('chat_files', 'message_id')