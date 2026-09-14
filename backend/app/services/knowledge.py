"""知识库服务 — CRUD、文档入库、向量检索。"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.models.knowledge import ChunkRecord, DocumentRecord, KnowledgeBaseRecord
from app.services.embedding import embed_query, embed_texts
from app.utils import files

logger = logging.getLogger(__name__)

_CHUNK_MAX_LEN = 500
_CHUNK_OVERLAP = 50


def _chunk_text(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\r\n\r\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]

    chunks: list[str] = []
    for para in paragraphs:
        if len(para) <= _CHUNK_MAX_LEN:
            chunks.append(para)
            continue
        start = 0
        while start < len(para):
            end = start + _CHUNK_MAX_LEN
            chunk = para[start:end]
            if chunk.strip():
                chunks.append(chunk.strip())
            start += _CHUNK_MAX_LEN - _CHUNK_OVERLAP

    merged: list[str] = []
    for chunk in chunks:
        if merged and len(chunk) < 30 and len(merged[-1]) + len(chunk) <= _CHUNK_MAX_LEN:
            merged[-1] += "\n" + chunk
        else:
            merged.append(chunk)
    return merged


# ── KnowledgeBase CRUD ─────────────────────────────────────────────────────


async def create_kb(name: str, description: str = "") -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        kb = KnowledgeBaseRecord(name=name, description=description)
        session.add(kb)
        await session.commit()
        await session.refresh(kb)
        return {"id": kb.id, "name": kb.name, "description": kb.description}


async def list_kbs() -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        rows = (await session.exec(
            select(KnowledgeBaseRecord).order_by(KnowledgeBaseRecord.id.desc())
        )).all()
    return [{"id": r.id, "name": r.name, "description": r.description} for r in rows]


async def delete_kb(kb_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        kb = await session.get(KnowledgeBaseRecord, kb_id)
        if not kb:
            return False
        await session.delete(kb)
        await session.commit()
    return True


# ── Document 入库 ─────────────────────────────────────────────────────────


async def ingest_document(kb_id: int, upload_id: str, filename: str) -> dict[str, Any]:
    """完整入库：读文件 → 切块 → 向量化 → 存储。"""
    async with AsyncSessionLocal() as session:
        doc = DocumentRecord(kb_id=kb_id, filename=filename, upload_id=upload_id, status="processing")
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        doc_id = doc.id

    try:
        raw_text = files.read_upload(upload_id)
        if not raw_text.strip():
            raise ValueError("文档正文为空")

        chunks = _chunk_text(raw_text)
        if not chunks:
            raise ValueError("切块后无有效内容")

        embeddings = await embed_texts(chunks)

        async with AsyncSessionLocal() as session:
            for i, (content, embedding) in enumerate(zip(chunks, embeddings)):
                session.add(ChunkRecord(
                    document_id=doc_id, content=content, embedding=embedding, chunk_index=i,
                ))
            doc = await session.get(DocumentRecord, doc_id)
            doc.status = "ready"
            doc.chunk_count = len(chunks)
            await session.commit()

        logger.info("[kb] ingested %s: %d chunks", filename, len(chunks))
        return {"doc_id": doc_id, "chunk_count": len(chunks)}

    except Exception as exc:
        async with AsyncSessionLocal() as session:
            doc = await session.get(DocumentRecord, doc_id)
            if doc:
                doc.status = "error"
                doc.error = str(exc)[:1000]
                await session.commit()
        logger.exception("[kb] ingest failed for %s", filename)
        raise


# ── 检索 ──────────────────────────────────────────────────────────────────


async def search_chunks(kb_id: int, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    query_embedding = await embed_query(query)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT c.content, d.filename, 1 - (c.embedding <=> :query_vec) AS similarity
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.kb_id = :kb_id AND c.embedding IS NOT NULL
                ORDER BY c.embedding <=> :query_vec
                LIMIT :top_k
            """),
            {"kb_id": kb_id, "query_vec": str(query_embedding), "top_k": top_k},
        )
        rows = result.fetchall()
    return [
        {"content": r[0], "filename": r[1], "similarity": round(float(r[2]), 4)}
        for r in rows
    ]


# ── 文档管理 ──────────────────────────────────────────────────────────────


async def list_all_documents() -> list[dict[str, Any]]:
    """返回所有文档，附带所属知识库名称。"""
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("""
                SELECT d.id, d.filename, d.status, d.chunk_count, d.error,
                       d.created_at, d.kb_id, k.name AS kb_name
                FROM documents d
                JOIN knowledge_bases k ON k.id = d.kb_id
                ORDER BY d.id DESC
            """),
        )).fetchall()
    return [
        {
            "id": r[0], "filename": r[1], "status": r[2],
            "chunk_count": r[3], "error": r[4],
            "created_at": r[5].isoformat() if r[5] else None,
            "kb_id": r[6], "kb_name": r[7],
        }
        for r in rows
    ]


async def list_documents(kb_id: int) -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        rows = (await session.exec(
            select(DocumentRecord)
            .where(DocumentRecord.kb_id == kb_id)
            .order_by(DocumentRecord.id.desc())
        )).all()
    return [
        {
            "id": d.id, "filename": d.filename, "status": d.status,
            "chunk_count": d.chunk_count, "error": d.error,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]


async def delete_document(doc_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if not doc:
            return False
        await session.delete(doc)
        await session.commit()
    return True
