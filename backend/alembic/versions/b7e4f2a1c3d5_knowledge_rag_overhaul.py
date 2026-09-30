"""knowledge RAG overhaul — Docling + BGE-M3 + hybrid search

Revision ID: b7e4f2a1c3d5
Revises: 031bc0cba40f
Create Date: 2026-09-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7e4f2a1c3d5'
down_revision: Union[str, Sequence[str], None] = '031bc0cba40f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 扩展 ──
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS zhparser")

    # ── documents 表新增字段 ──
    op.add_column('documents', sa.Column('file_path', sa.String(length=500), nullable=True))
    op.add_column('documents', sa.Column('file_size', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('file_ext', sa.String(length=20), nullable=True))
    op.add_column('documents', sa.Column('content_hash', sa.String(length=64), nullable=True))
    op.add_column('documents', sa.Column('parse_duration_ms', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.create_index(op.f('ix_documents_content_hash'), 'documents', ['content_hash'], unique=False)

    # ── chunks 表新增字段 ──
    op.add_column('chunks', sa.Column('file_name', sa.String(length=255), nullable=True))
    op.add_column('chunks', sa.Column('page_numbers', postgresql.ARRAY(sa.INTEGER()), nullable=True))
    op.add_column('chunks', sa.Column('heading_context', sa.Text(), nullable=True))
    op.add_column('chunks', sa.Column('raw_content', sa.Text(), nullable=True))
    op.add_column('chunks', sa.Column('enriched_content', sa.Text(), nullable=True))
    op.add_column('chunks', sa.Column('tsv_content', postgresql.TSVECTOR(), nullable=True))

    # ── embedding 维度变更 768 → 1024 ──
    # 先清空现有向量（维度不兼容），然后改列类型
    op.execute("UPDATE chunks SET embedding = NULL")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1024)")

    # ── HNSW 向量索引（余弦相似度）──
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops)")

    # ── 中文全文搜索配置 + GIN 索引 + 触发器 ──
    op.execute("""
        DO $$
        BEGIN
            -- 停词字典
            IF NOT EXISTS (SELECT 1 FROM pg_ts_dict WHERE dictname = 'chinese_stop') THEN
                CREATE TEXT SEARCH DICTIONARY chinese_stop (TEMPLATE = simple, STOPWORDS = chinese);
            END IF;
            -- 搜索配置
            IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'chinese') THEN
                CREATE TEXT SEARCH CONFIGURATION chinese (PARSER = zhparser);
            END IF;
            ALTER TEXT SEARCH CONFIGURATION chinese
                ALTER MAPPING FOR n,v,a,i,e,l,t
                WITH chinese_stop, simple;
        END $$;
    """)
    op.create_index(op.f('ix_chunks_tsv_content'), 'chunks', ['tsv_content'], postgresql_using='gin')
    op.execute("""
        CREATE TRIGGER tsvectorupdate
        BEFORE INSERT OR UPDATE ON chunks
        FOR EACH ROW EXECUTE FUNCTION
        tsvector_update_trigger(tsv_content, 'public.chinese', raw_content)
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tsvectorupdate ON chunks")
    op.drop_index(op.f('ix_chunks_tsv_content'), table_name='chunks')
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(768)")
    op.drop_column('chunks', 'tsv_content')
    op.drop_column('chunks', 'enriched_content')
    op.drop_column('chunks', 'raw_content')
    op.drop_column('chunks', 'heading_context')
    op.drop_column('chunks', 'page_numbers')
    op.drop_column('chunks', 'file_name')
    op.drop_index(op.f('ix_documents_content_hash'), table_name='documents')
    op.drop_column('documents', 'updated_at')
    op.drop_column('documents', 'parse_duration_ms')
    op.drop_column('documents', 'content_hash')
    op.drop_column('documents', 'file_ext')
    op.drop_column('documents', 'file_size')
    op.drop_column('documents', 'file_path')