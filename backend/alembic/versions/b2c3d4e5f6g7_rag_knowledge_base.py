"""rag knowledge base — knowledge_bases + documents + chunks with pgvector

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # knowledge_bases
    op.create_table(
        'knowledge_bases',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), server_default=''),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # agents 新增 kb_id
    op.add_column('agents', sa.Column('kb_id', sa.Integer(), sa.ForeignKey('knowledge_bases.id', ondelete='SET NULL'), nullable=True))

    # documents
    op.create_table(
        'documents',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kb_id', sa.Integer(), sa.ForeignKey('knowledge_bases.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('upload_id', sa.String(200), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='processing'),
        sa.Column('chunk_count', sa.Integer(), server_default='0'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    # chunks
    op.create_table(
        'chunks',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(768), nullable=True),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )

    op.execute(
        "CREATE INDEX idx_chunks_embedding ON chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)"
    )


def downgrade() -> None:
    op.drop_table('chunks')
    op.drop_table('documents')
    op.drop_column('agents', 'kb_id')
    op.drop_table('knowledge_bases')
    op.execute("DROP EXTENSION IF EXISTS vector")
