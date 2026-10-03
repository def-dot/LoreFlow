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
from app.models.knowledge import ChunkRecord, DocumentRecord, DocumentStatus, DocumentTagRecord, TagRecord
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


# ── 标签 CRUD ───────────────────────────────────────────────────────


async def create_tag(name: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        tag = TagRecord(name=name)
        session.add(tag)
        await session.commit()
        await session.refresh(tag)
        return {"id": tag.id, "name": tag.name}


async def update_tag(tag_id: int, name: str) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as session:
        tag = await session.get(TagRecord, tag_id)
        if tag is None:
            return None
        tag.name = name
        session.add(tag)
        await session.commit()
        await session.refresh(tag)
        return {"id": tag.id, "name": tag.name}


async def list_tags() -> list[dict[str, Any]]:
    async with AsyncSessionLocal() as session:
        rows = (await session.exec(
            select(TagRecord).order_by(TagRecord.id)
        )).all()
    return [{"id": r.id, "name": r.name} for r in rows]


async def delete_tag(tag_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        tag = await session.get(TagRecord, tag_id)
        if tag is None:
            return False
        # 清理关联
        await session.exec(
            delete(DocumentTagRecord).where(DocumentTagRecord.tag_id == tag_id)
        )
        await session.delete(tag)
        await session.commit()
    return True


async def _get_doc_tags(session, doc_ids: list[int]) -> dict[int, list[dict]]:
    """批量查询文档标签，返回 {doc_id: [{id, name}, ...]}"""
    if not doc_ids:
        return {}
    rows = (await session.exec(
        select(DocumentTagRecord, TagRecord)
        .join(TagRecord, DocumentTagRecord.tag_id == TagRecord.id)
        .where(DocumentTagRecord.document_id.in_(doc_ids))
    )).all()
    result: dict[int, list[dict]] = {did: [] for did in doc_ids}
    for dt, tag in rows:
        result[dt.document_id].append({"id": tag.id, "name": tag.name})
    return result


# ── 自动打标签 ──────────────────────────────────────────────────────


async def auto_tag_document(document_id: int, text_preview: str) -> None:
    """解析完成后自动打标签（仅当文档无手动标签且已有标签时）。"""
    async with AsyncSessionLocal() as session:
        # 检查是否已有手动标签
        existing = (await session.exec(
            select(DocumentTagRecord).where(DocumentTagRecord.document_id == document_id)
        )).first()
        if existing:
            return  # 已有标签，跳过

        # 获取所有可用标签
        tags = (await session.exec(select(TagRecord))).all()
        if not tags:
            return  # 无标签可选

    tag_names = [t.name for t in tags]
    tag_list_str = "、".join(tag_names)

    from app.services.llm import llm_chat_call

    try:
        result = await llm_chat_call(
            model=None,  # 使用默认模型
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"你是一个文档分类助手。根据文档内容，从以下标签中选择最匹配的（可多选，用逗号分隔）：\n"
                        f"{tag_list_str}\n\n"
                        f"只返回标签名称，不要其他文字。如果都不匹配，返回空。"
                    ),
                },
                {"role": "user", "content": text_preview[:2000]},
            ],
        )
        content = result.get("content", "").strip()
        if not content:
            return

        # 解析 LLM 返回的标签名
        selected = [n.strip() for n in content.split(",") if n.strip()]
        if not selected:
            return

        name_to_id = {t.name: t.id for t in tags}
        async with AsyncSessionLocal() as session:
            for name in selected:
                if name in name_to_id:
                    session.add(DocumentTagRecord(document_id=document_id, tag_id=name_to_id[name]))
            await session.commit()

        logger.info("[auto-tag] doc#%d tagged: %s", document_id, selected)
    except Exception:
        logger.warning("[auto-tag] doc#%d failed, skipped", document_id, exc_info=True)


# ── 文档入库（异步） ───────────────────────────────────────────────


async def ingest_document(upload_id: str, filename: str, *, tag_ids: list[int] | None = None) -> dict[str, Any]:
    """创建文档记录并入队异步解析。"""
    suffix = os.path.splitext(filename)[1].lower()
    file_path = str(settings.UPLOADS_DIR / upload_id)
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None

    async with AsyncSessionLocal() as session:
        doc = DocumentRecord(
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

        # 写入标签关联
        if tag_ids:
            for tid in tag_ids:
                session.add(DocumentTagRecord(document_id=doc_id, tag_id=tid))
            await session.commit()

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

        # 自动打标签（取前几个 chunk 的 enriched_text 作为预览）
        preview = "\n".join(c.get("enriched_text", "") for c in chunks[:3])
        await auto_tag_document(document_id, preview)

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
            "id": doc.id, "filename": doc.filename,
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


async def search_chunks(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """混合检索：BM25 + 向量 + RRF 融合 + Reranker 精排。"""
    t0 = time.perf_counter()
    query_vec = await embed_query(query)
    logger.info("[perf] query_vec: %.2fs", time.perf_counter() - t0)

    # 第一阶段：混合召回
    t0 = time.perf_counter()
    recalls = await _get_chunks_hybrid_rrf(
        query, query_vec,
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
    vec_threshold: float = 0.35,
    limit: int = 100,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    """单 SQL 完成稠密向量 + tsvector BM25 双路召回 + RRF 融合。"""
    query_vec_str = _vector_to_str(query_vec)

    hybrid_sql = text("""
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
            FROM chunks c, qv
            WHERE c.embedding IS NOT NULL
              AND 1.0 - (c.embedding <=> qv.v) > :threshold
            ORDER BY c.embedding <=> qv.v
            LIMIT :limit
        ),
        sparse_search AS (
            SELECT c.id, c.document_id, c.file_name, c.raw_content, c.enriched_content,
                   ROW_NUMBER() OVER (ORDER BY ts_rank(c.tsv_content, qt.q) DESC) AS rank
            FROM chunks c, qt
            WHERE c.tsv_content @@ qt.q
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
    q: str = "",
    tag_ids: list[int] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """返回所有文档（分页 + 状态/文件名/标签筛选）。"""
    async with AsyncSessionLocal() as session:
        base = select(DocumentRecord)
        count_base = select(func.count(DocumentRecord.id))

        if status:
            base = base.where(DocumentRecord.status == status)
            count_base = count_base.where(DocumentRecord.status == status)
        if q:
            like = f"%{q}%"
            base = base.where(DocumentRecord.filename.ilike(like))
            count_base = count_base.where(DocumentRecord.filename.ilike(like))
        if tag_ids:
            tagged = (
                select(DocumentTagRecord.document_id)
                .where(DocumentTagRecord.tag_id.in_(tag_ids))
            )
            base = base.where(DocumentRecord.id.in_(tagged))
            count_base = count_base.where(DocumentRecord.id.in_(tagged))

        total = (await session.execute(count_base)).scalar() or 0
        rows = (await session.execute(
            base.order_by(DocumentRecord.id.desc()).offset(offset).limit(limit)
        )).scalars().all()

        # 批量查标签
        doc_ids = [d.id for d in rows]
        tag_map = await _get_doc_tags(session, doc_ids)

    items = [
        {
            "id": d.id, "filename": d.filename, "status": d.status,
            "chunk_count": d.chunk_count, "error": d.error,
            "parse_duration_ms": d.parse_duration_ms, "file_size": d.file_size,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "tags": tag_map.get(d.id, []),
        }
        for d in rows
    ]
    return items, total


async def get_document(doc_id: int) -> dict[str, Any] | None:
    async with AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if doc is None:
            return None
        return {
            "id": doc.id, "filename": doc.filename,
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