"""知识库服务 — CRUD、文档入库（异步）、混合检索（BM25 + Vector + RRF + Reranker）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, text
from sqlmodel import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.knowledge import ChunkRecord, DocumentRecord, DocumentStatus, KnowledgeBaseRecord
from app.services.embedding import embed_query, embed_texts
from app.services.rerank import rerank
from app.utils import files

logger = logging.getLogger(__name__)


# ── 工具函数 ────────────────────────────────────────────────────────


def _vector_to_str(vec: list[float]) -> str:
    """将向量列表转换为 PostgreSQL vector 字面量。"""
    return "[" + ",".join(str(v) for v in vec) + "]"


def compute_content_hash(data: bytes) -> str:
    """计算文件内容的 SHA-256 哈希。"""
    return hashlib.sha256(data).hexdigest()


# ── KnowledgeBase CRUD ──────────────────────────────────────────────


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


_DEFAULT_KB_NAME = "默认知识库"


async def get_or_create_default_kb() -> int:
    """获取或创建默认知识库，返回其 ID。"""
    async with AsyncSessionLocal() as session:
        kb = (await session.exec(
            select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.name == _DEFAULT_KB_NAME)
        )).first()
        if kb:
            return kb.id  # type: ignore[return-value]
        new_kb = KnowledgeBaseRecord(name=_DEFAULT_KB_NAME, description="自动创建的默认知识库")
        session.add(new_kb)
        await session.commit()
        await session.refresh(new_kb)
        return new_kb.id  # type: ignore[return-value]


async def delete_kb(kb_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        kb = await session.get(KnowledgeBaseRecord, kb_id)
        if not kb:
            return False
        await session.delete(kb)
        await session.commit()
    return True


# ── 文档入库（异步） ───────────────────────────────────────────────


async def ingest_document(kb_id: int, upload_id: str, filename: str) -> dict[str, Any]:
    """创建文档记录并入队异步解析。"""
    suffix = os.path.splitext(filename)[1].lower()
    file_path = str(settings.UPLOADS_DIR / upload_id)
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None

    async with AsyncSessionLocal() as session:
        doc = DocumentRecord(
            kb_id=kb_id,
            filename=filename,
            upload_id=upload_id,
            file_path=file_path,
            file_size=file_size,
            file_ext=suffix,
            status=DocumentStatus.PENDING,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        doc_id = doc.id

    # 入队异步解析
    from app.utils.arq import enqueue_parse
    await enqueue_parse(doc_id)

    logger.info("[kb] document %d enqueued for parsing: %s", doc_id, filename)
    return {"doc_id": doc_id, "status": DocumentStatus.PENDING}


async def parse_document(document_id: int) -> None:
    """文档解析 — 分块 — 入库（Arq worker 调用）。

    并发安全由 with_lock 装饰器保证（Redis SET NX）。
    """
    proc = None
    try:
        # 1. 读取文档信息
        doc = await _get_document(document_id)
        if doc is None:
            logger.warning("Doc#%d not found in DB, skip", document_id)
            return

        file_path = doc.get("file_path")
        if not file_path or not os.path.exists(file_path):
            await _update_document_status(document_id, DocumentStatus.FAILED,
                                          error=f"源文件不存在: {file_path}")
            return

        t0 = time.time()
        await _update_document_status(document_id, DocumentStatus.PROCESSING)

        # 2. 文档解析（子进程，隔离 Docling 内存）
        output_fd, output_path = tempfile.mkstemp(suffix=".json")
        os.close(output_fd)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.services.docling_chunk", file_path,
            "--output", output_path,
            "--doc-id", str(document_id),
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        try:
            await proc.communicate()

            with open(output_path, encoding="utf-8") as f:
                result = json.load(f)
            if not result["ok"]:
                raise RuntimeError(f"解析失败: {result['error']}")
            chunks = result["data"]
        finally:
            os.remove(output_path)

        # 3. 入库
        if not chunks:
            await _update_document_status(document_id, DocumentStatus.FAILED,
                                          error="未解析出文本块")
            return

        inserted_count = await _insert_chunks(chunks, document_id=document_id)
        if inserted_count == 0:
            await _update_document_status(document_id, DocumentStatus.FAILED,
                                          error="文本块入库失败")
            return

        duration_ms = int((time.time() - t0) * 1000)
        await _update_document_status(
            document_id, DocumentStatus.COMPLETED,
            chunk_count=inserted_count, parse_duration_ms=duration_ms,
        )

    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            proc.kill()
        await _update_document_status(document_id, DocumentStatus.CANCELLED, error="用户取消")
        raise
    except Exception as exc:
        await _update_document_status(document_id, DocumentStatus.FAILED,
                                      error=str(exc)[:2000])


async def _get_document(document_id: int) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, document_id)
        if doc is None:
            return None
        return {
            "id": doc.id, "kb_id": doc.kb_id, "filename": doc.filename,
            "upload_id": doc.upload_id, "file_path": doc.file_path,
            "status": doc.status,
        }


async def _update_document_status(
    document_id: int,
    status: str,
    chunk_count: int = 0,
    error: str | None = None,
    parse_duration_ms: int | None = None,
) -> None:
    async with AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, document_id)
        if doc is None:
            return
        doc.status = status
        doc.updated_at = datetime.now()
        if status == DocumentStatus.COMPLETED:
            doc.chunk_count = chunk_count
            doc.error = None
            doc.parse_duration_ms = parse_duration_ms
        if error is not None:
            doc.error = error
        session.add(doc)
        await session.commit()


async def _insert_chunks(chunks: list[dict[str, Any]], document_id: int) -> int:
    """将切片批量写入数据库（含稠密向量，tsv_content 由 PostgreSQL 触发器自动生成）。"""
    if not chunks:
        return 0

    texts = [c["enriched_text"] for c in chunks]
    dense_vectors = await embed_texts(texts)

    async with AsyncSessionLocal() as session:
        failed = 0
        for chunk_data, dense_vec in zip(chunks, dense_vectors, strict=True):
            try:
                async with session.begin_nested():
                    meta = chunk_data["metadata"]
                    await session.execute(
                        text(
                            """
                            INSERT INTO chunks
                                (document_id, file_name, page_numbers, heading_context,
                                 raw_content, enriched_content, embedding)
                            VALUES
                                (:document_id, :file_name, CAST(:page_numbers AS INTEGER[]),
                                 :heading_context, :raw_content, :enriched_content,
                                 CAST(:embedding AS vector))
                            """
                        ),
                        {
                            "document_id": document_id,
                            "file_name": meta["source_file"],
                            "page_numbers": meta["page_numbers"],
                            "heading_context": meta["heading_context"],
                            "raw_content": chunk_data["raw_text"],
                            "enriched_content": chunk_data["enriched_text"],
                            "embedding": _vector_to_str(dense_vec),
                        },
                    )
            except Exception:
                failed += 1
                logger.exception("Failed to insert chunk for document %d", document_id)

        await session.commit()
        logger.info("Stored %d/%d chunks (document_id=%d, %d failed)",
                     len(chunks) - failed, len(chunks), document_id, failed)
        return len(chunks) - failed


# ── 检索（BM25 + Vector + RRF + Reranker） ─────────────────────────


async def search_chunks(kb_id: int | None, query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """混合检索：BM25 + 向量 + RRF 融合 + Reranker 精排。

    kb_id 为 None 时搜索全部文档；有值时只搜该知识库。
    """
    t0 = time.perf_counter()
    query_vec = await embed_query(query)
    logger.info("[perf] query_vec: %.2fs", time.perf_counter() - t0)

    # 第一阶段：混合召回
    t0 = time.perf_counter()
    recalls = await _get_chunks_hybrid_rrf(
        query, query_vec, kb_id,
        vec_threshold=settings.VECTOR_THRESHOLD,
        limit=settings.RECALL_COUNT,
        rrf_k=settings.RRF_K,
    )
    logger.info("[perf] hybrid_rrf: %.2fs (%d results)", time.perf_counter() - t0, len(recalls))

    if not recalls:
        return []

    # 第二阶段：Reranker 精排
    rerank_candidates = recalls[:settings.RERANK_COUNT]
    passages = [c["content"] for c in rerank_candidates]
    t0 = time.perf_counter()
    scores = await rerank(query, passages)
    logger.info("[perf] rerank: %.2fs", time.perf_counter() - t0)

    for item, score in zip(rerank_candidates, scores):
        item["similarity"] = round(score, 4)

    # 过滤 + 排序
    ranked = [c for c in rerank_candidates if c["similarity"] >= settings.RERANK_THRESHOLD]
    ranked.sort(key=lambda x: x["similarity"], reverse=True)

    return [
        {"content": r["content"], "filename": r["filename"], "similarity": r["similarity"]}
        for r in ranked[:top_k]
    ]


async def _get_chunks_hybrid_rrf(
    query_text: str,
    query_vec: list[float],
    kb_id: int | None = None,
    vec_threshold: float = 0.35,
    limit: int = 100,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """单 SQL 完成稠密向量 + tsvector BM25 双路召回 + RRF 融合。

    kb_id 为 None 时不加知识库过滤，搜索全部文档。
    """
    query_vec_str = _vector_to_str(query_vec)
    kb_filter = "AND d.kb_id = :kb_id" if kb_id is not None else ""

    hybrid_sql = text(f"""
        WITH qv AS (
            SELECT CAST(:qvec AS vector) AS v
        ),
        qt AS (
            SELECT to_tsquery('chinese', array_to_string(
                tsvector_to_array(to_tsvector('chinese', :query)), ' | '
            )) AS q
        ),
        dense_search AS (
            SELECT c.id, c.document_id, c.file_name, c.raw_content, c.enriched_content,
                   ROW_NUMBER() OVER (ORDER BY c.embedding <=> qv.v) AS rank
            FROM chunks c, qv, documents d
            WHERE c.document_id = d.id
              {kb_filter}
              AND c.embedding IS NOT NULL
              AND 1.0 - (c.embedding <=> qv.v) > :threshold
            ORDER BY c.embedding <=> qv.v
            LIMIT :limit
        ),
        sparse_search AS (
            SELECT c.id, c.document_id, c.file_name, c.raw_content, c.enriched_content,
                   ROW_NUMBER() OVER (ORDER BY ts_rank(c.tsv_content, qt.q) DESC) AS rank
            FROM chunks c, qt, documents d
            WHERE c.document_id = d.id
              {kb_filter}
              AND c.tsv_content @@ qt.q
            ORDER BY ts_rank(c.tsv_content, qt.q) DESC
            LIMIT :limit
        )
        SELECT
            COALESCE(d.id, s.id)                        AS id,
            COALESCE(d.document_id, s.document_id)      AS document_id,
            COALESCE(d.file_name, s.file_name)          AS file_name,
            COALESCE(d.enriched_content, s.enriched_content)      AS content,
            COALESCE(1.0 / (:rrf_k + d.rank), 0.0)
                + COALESCE(1.0 / (:rrf_k + s.rank), 0.0) AS score
        FROM dense_search d
        FULL OUTER JOIN sparse_search s ON d.id = s.id
        ORDER BY score DESC
    """)

    params: dict[str, Any] = {
        "qvec": query_vec_str,
        "query": query_text,
        "threshold": vec_threshold,
        "limit": limit,
        "rrf_k": rrf_k,
    }
    if kb_id is not None:
        params["kb_id"] = kb_id

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(hybrid_sql, params)).fetchall()

    return [
        {"id": r[0], "document_id": r[1], "filename": r[2], "content": r[3], "similarity": float(r[4])}
        for r in rows
    ]


# ── 文档管理 ────────────────────────────────────────────────────────


async def list_all_documents(
    limit: int = 50,
    offset: int = 0,
    status: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """返回所有文档（分页 + 状态筛选），附带所属知识库名称。"""
    async with AsyncSessionLocal() as session:
        base = (
            select(DocumentRecord)
            .join(KnowledgeBaseRecord, KnowledgeBaseRecord.id == DocumentRecord.kb_id)
        )
        count_base = select(func.count(DocumentRecord.id))

        if status:
            base = base.where(DocumentRecord.status == status)
            count_base = count_base.where(DocumentRecord.status == status)

        total = (await session.execute(count_base)).scalar() or 0
        rows = (await session.execute(
            base.order_by(DocumentRecord.id.desc()).offset(offset).limit(limit)
        )).scalars().all()

        # 批量查 kb_name
        kb_ids = {r.kb_id for r in rows}
        kb_map: dict[int, str] = {}
        if kb_ids:
            kbs = (await session.exec(
                select(KnowledgeBaseRecord).where(KnowledgeBaseRecord.id.in_(kb_ids))
            )).all()
            kb_map = {kb.id: kb.name for kb in kbs}

    items = [
        {
            "id": d.id, "filename": d.filename, "status": d.status,
            "chunk_count": d.chunk_count, "error": d.error,
            "parse_duration_ms": d.parse_duration_ms,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "kb_id": d.kb_id, "kb_name": kb_map.get(d.kb_id, ""),
        }
        for d in rows
    ]
    return items, total


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
            "parse_duration_ms": d.parse_duration_ms,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in rows
    ]


async def get_document(doc_id: int) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if doc is None:
            return None
        return {
            "id": doc.id, "kb_id": doc.kb_id, "filename": doc.filename,
            "upload_id": doc.upload_id, "file_path": doc.file_path,
            "file_size": doc.file_size, "file_ext": doc.file_ext,
            "status": doc.status, "chunk_count": doc.chunk_count,
            "error": doc.error, "content_hash": doc.content_hash,
            "parse_duration_ms": doc.parse_duration_ms,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
        }


async def delete_document(doc_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if not doc:
            return False
        # 删除关联切片
        await session.execute(delete(ChunkRecord).where(ChunkRecord.document_id == doc_id))
        await session.delete(doc)
        await session.commit()
    return True


async def get_document_status_counts() -> dict[str, int]:
    """按状态统计文档数量。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(DocumentRecord.id), DocumentRecord.status)
            .group_by(DocumentRecord.status)
        )
        counts = {row[1]: row[0] for row in result.all()}
    return {
        "pending": counts.get(DocumentStatus.PENDING, 0),
        "processing": counts.get(DocumentStatus.PROCESSING, 0),
        "completed": counts.get(DocumentStatus.COMPLETED, 0),
        "failed": counts.get(DocumentStatus.FAILED, 0),
        "cancelled": counts.get(DocumentStatus.CANCELLED, 0),
    }


async def list_chunks(
    document_id: int, page: int = 1, page_size: int = 30, q: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """列出文档的切片（分页 + 可选搜索）。"""
    async with AsyncSessionLocal() as session:
        filters = [ChunkRecord.document_id == document_id]
        if q:
            like = f"%{q}%"
            filters.append(
                ChunkRecord.raw_content.ilike(like) | ChunkRecord.heading_context.ilike(like)  # type: ignore[attr-defined]
            )

        total = (await session.execute(
            select(func.count()).select_from(ChunkRecord).where(*filters)
        )).scalar_one()

        rows = (await session.execute(
            select(ChunkRecord)
            .where(*filters)
            .order_by(ChunkRecord.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )).scalars().all()

    items = [
        {
            "id": c.id, "document_id": c.document_id, "file_name": c.file_name,
            "page_numbers": c.page_numbers or [],
            "heading_context": c.heading_context or "",
            "raw_content": c.raw_content or "",
        }
        for c in rows
    ]
    return items, total


async def cancel_document(doc_id: int) -> bool:
    """取消正在解析的文档。"""
    from app.utils.arq import cancel_parse
    ok = await cancel_parse(doc_id)
    if ok:
        await _update_document_status(doc_id, DocumentStatus.CANCELLED, error="用户取消")
    return ok


async def retry_document(doc_id: int) -> bool:
    """重试失败或已取消的文档。"""
    async with AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if doc is None or doc.status not in (DocumentStatus.FAILED, DocumentStatus.CANCELLED):
            return False
        doc.status = DocumentStatus.PENDING
        doc.error = None
        doc.updated_at = datetime.now()
        session.add(doc)
        await session.commit()

    from app.utils.arq import delete_job, enqueue_parse, doc_job_id
    await delete_job(doc_job_id(doc_id))
    await enqueue_parse(doc_id)
    return True


async def reconcile_stuck(redis: Any = None) -> None:
    """重扫卡住的文档并重新入队（分布式安全）。"""
    from app.utils.arq import delete_job, doc_job_id, doc_lock_key, enqueue_parse

    async with AsyncSessionLocal() as session:
        processing = (await session.exec(
            select(DocumentRecord).where(DocumentRecord.status == DocumentStatus.PROCESSING)
        )).all()

    for doc in processing:
        if redis is not None:
            try:
                lock_held = await redis.exists(doc_lock_key(doc.id))
            except Exception:
                lock_held = False
            if lock_held:
                continue  # 锁还在 → worker 正常运行
        await delete_job(doc_job_id(doc.id))
        if await enqueue_parse(doc.id):
            logger.info("reconcile: re-enqueued orphan doc %d", doc.id)