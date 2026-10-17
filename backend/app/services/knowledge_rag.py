"""RAG 相关 — 文档解析入库、混合检索（BM25 + Vector + RRF + Reranker）。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.core import database
from app.core.config import settings
from app.models.knowledge import DocumentRecord, DocumentStatus
from app.services.embedding import embed_query, embed_texts
from app.services.rerank import rerank

logger = logging.getLogger(__name__)


def _vector_to_str(vec: list[float]) -> str:
    """将向量列表转换为 PostgreSQL vector 字面量。"""
    return "[" + ",".join(str(v) for v in vec) + "]"


# ── 文档解析入库 ────────────────────────────────────────────────────


async def _update_document_status(
    document_id: int,
    status: str,
    chunk_count: int = 0,
    error: str | None = None,
    parse_duration_ms: int | None = None,
) -> None:
    async with database.AsyncSessionLocal() as session:
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

    async with database.AsyncSessionLocal() as session:
        failed = 0
        for chunk_data, dense_vec in zip(chunks, dense_vectors, strict=True):
            try:
                async with session.begin_nested():
                    meta = chunk_data["metadata"]
                    await session.execute(
                        text(
                            """
                            INSERT INTO chunks
                                (document_id, file_path, page_numbers, heading_context,
                                 raw_content, enriched_content, embedding)
                            VALUES
                                (:document_id, :file_path, CAST(:page_numbers AS INTEGER[]),
                                 :heading_context, :raw_content, :enriched_content,
                                 CAST(:embedding AS vector))
                            """
                        ),
                        {
                            "document_id": document_id,
                            "file_path": meta["source_file"],
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


async def parse_document(document_id: int) -> None:
    """文档解析 — 分块 — 入库（Arq worker 调用）。

    并发安全由 with_lock 装饰器保证（Redis SET NX）。
    """
    proc = None
    try:
        async with database.AsyncSessionLocal() as session:
            doc = await session.get(DocumentRecord, document_id)
        if doc is None:
            logger.warning("Doc#%d not found in DB, skip", document_id)
            return

        file_path = doc.file_path
        if not file_path or not os.path.exists(file_path):
            await _update_document_status(document_id, DocumentStatus.FAILED,
                                          error=f"源文件不存在: {file_path}")
            return

        t0 = time.time()
        await _update_document_status(document_id, DocumentStatus.PROCESSING)

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


# ── 检索（BM25 + Vector + RRF + Reranker） ─────────────────────────


async def search_chunks(
    query: str,
    top_k: int = 5,
    *,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """混合检索：BM25 + 向量 + RRF 融合 + Reranker 精排。"""
    t0 = time.perf_counter()
    query_vec = await embed_query(query)
    logger.info("[perf] query_vec: %.2fs", time.perf_counter() - t0)

    t0 = time.perf_counter()
    recalls = await _get_chunks_hybrid_rrf(
        query, query_vec,
        vec_threshold=settings.VECTOR_THRESHOLD,
        limit=settings.RECALL_COUNT,
        rrf_k=settings.RRF_K,
        tags=tags,
    )
    logger.info("[perf] hybrid_rrf: %.2fs (%d results)", time.perf_counter() - t0, len(recalls))

    if not recalls:
        return []

    rerank_candidates = recalls[:settings.RERANK_COUNT]
    passages = [c["content"] for c in rerank_candidates]
    t0 = time.perf_counter()
    scores = await rerank(query, passages)
    logger.info("[perf] rerank: %.2fs", time.perf_counter() - t0)

    for item, score in zip(rerank_candidates, scores):
        item["similarity"] = round(score, 4)

    ranked = [c for c in rerank_candidates if c["similarity"] >= settings.RERANK_THRESHOLD]
    ranked.sort(key=lambda x: x["similarity"], reverse=True)

    return [
        {"chunk_id": r["id"], "content": r["content"], "file_path": r["file_path"], "similarity": r["similarity"]}
        for r in ranked[:top_k]
    ]


async def search_multi(
    queries: list[str],
    tags: list[str] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """并发检索多个 query，多 query 时自动去重，返回结构化结果列表。"""

    async def _retrieve_one(q: str):
        return await search_chunks(q, top_k=top_k, tags=tags)

    all_results = await asyncio.gather(*[_retrieve_one(q) for q in queries])

    if not all_results:
        return []

    seen: dict[int, dict] = {}
    for results in all_results:
        for r in results:
            cid = r["chunk_id"]
            if cid not in seen or r["similarity"] > seen[cid]["similarity"]:
                seen[cid] = r

    return sorted(seen.values(), key=lambda x: x["similarity"], reverse=True)[:top_k]


def format_sources(sources: list[dict[str, Any]]) -> str:
    """将检索结果列表格式化为可读文本（使用 chunk_id 作为唯一标识）。"""
    if not sources:
        return "（未检索到相关内容）"
    parts = [f"[#{r['chunk_id']}] 来源：{r['file_path']}\n{r['content']}" for r in sources]
    return "\n\n".join(parts)


async def _get_chunks_hybrid_rrf(
    query_text: str,
    query_vec: list[float],
    vec_threshold: float = 0.35,
    limit: int = 100,
    rrf_k: int = 60,
    tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    """单 SQL 完成稠密向量 + tsvector BM25 双路召回 + RRF 融合。"""
    query_vec_str = _vector_to_str(query_vec)

    scope_filter = ""
    if tags:
        scope_filter = (
            "AND c.document_id IN ("
            "  SELECT dt.document_id FROM document_tags dt"
            "  JOIN tags t ON t.id = dt.tag_id"
            "  WHERE t.name = ANY(:tags)"
            ")"
        )

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
            SELECT c.id, c.document_id, c.file_path, c.raw_content, c.enriched_content,
                   ROW_NUMBER() OVER (ORDER BY c.embedding <=> qv.v) AS rank
            FROM chunks c, qv
            WHERE c.embedding IS NOT NULL
              AND 1.0 - (c.embedding <=> qv.v) > :threshold
              {scope_filter}
            ORDER BY c.embedding <=> qv.v
            LIMIT :limit
        ),
        sparse_search AS (
            SELECT c.id, c.document_id, c.file_path, c.raw_content, c.enriched_content,
                   ROW_NUMBER() OVER (ORDER BY ts_rank(c.tsv_content, qt.q) DESC) AS rank
            FROM chunks c, qt
            WHERE c.tsv_content @@ qt.q
              {scope_filter}
            ORDER BY ts_rank(c.tsv_content, qt.q) DESC
            LIMIT :limit
        )
        SELECT
            COALESCE(d.id, s.id)                        AS id,
            COALESCE(d.document_id, s.document_id)      AS document_id,
            COALESCE(d.file_path, s.file_path)          AS filename,
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
    if tags:
        params["tags"] = tags

    async with database.AsyncSessionLocal() as session:
        rows = (await session.execute(hybrid_sql, params)).fetchall()

    return [
        {"id": r[0], "document_id": r[1], "file_path": r[2], "content": r[3], "similarity": float(r[4])}
        for r in rows
    ]