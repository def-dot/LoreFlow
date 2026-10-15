"""知识库服务 — 文档 CRUD、入库、文档管理、标签。

持久化函数调用时才查找 ``database.AsyncSessionLocal``（同 services/runs.py），
conftest 直接换掉该全局即可换库。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import ConfigDict, Field, create_model
from sqlalchemy import delete, func
from sqlmodel import select

from app.core import database
from app.core.config import settings
from app.models.knowledge import ChunkRecord, DocumentRecord, DocumentStatus, DocumentTagRecord, TagRecord
from app.schemas.knowledge import ChunkItem, DocumentListItem
from app.services.knowledge_rag import (
    _insert_chunks,
    _update_document_status,
    format_sources,
    parse_document,
    search_chunks,
    search_multi,
)

logger = logging.getLogger(__name__)


# ── 标签 CRUD ───────────────────────────────────────────────────────


async def create_tag(name: str) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise ValueError("标签名不能为空")
    async with database.AsyncSessionLocal() as session:
        exists = (await session.exec(select(TagRecord).where(TagRecord.name == name))).first()
        if exists is not None:
            raise ValueError("标签已存在")
        tag = TagRecord(name=name)
        session.add(tag)
        await session.commit()
        await session.refresh(tag)
    return {"name": tag.name}


async def update_tag(old_name: str, new_name: str) -> dict[str, Any] | None:
    """重命名标签；关联按 tag_id 走，文档侧无需改动。"""
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("标签名不能为空")
    async with database.AsyncSessionLocal() as session:
        tag = (await session.exec(select(TagRecord).where(TagRecord.name == old_name))).first()
        if tag is None:
            return None
        if new_name != old_name:
            dup = (await session.exec(select(TagRecord).where(TagRecord.name == new_name))).first()
            if dup is not None:
                raise ValueError("标签已存在")
            tag.name = new_name
            session.add(tag)
            await session.commit()
    return {"name": new_name}


async def list_tags() -> list[str]:
    async with database.AsyncSessionLocal() as session:
        rows = (await session.exec(
            select(TagRecord.name).order_by(TagRecord.id)
        )).all()
    return list(rows)


async def delete_tag(name: str) -> bool:
    async with database.AsyncSessionLocal() as session:
        tag = (await session.exec(select(TagRecord).where(TagRecord.name == name))).first()
        if tag is None:
            return False
        await session.exec(
            delete(DocumentTagRecord).where(DocumentTagRecord.tag_id == tag.id)
        )
        await session.delete(tag)
        await session.commit()
    return True


# ── 自动打标签 ──────────────────────────────────────────────────────


async def infer_tags_for_text(text_preview: str) -> list[str]:
    """LLM 对比已有标签推断文本所属分类，返回标签名列表；无标签或失败返回空列表。"""
    async with database.AsyncSessionLocal() as session:
        tag_names = (await session.exec(select(TagRecord.name))).all()
    if not tag_names:
        return []
    tag_names = list(tag_names)

    TagSelection = create_model(
        "TagSelection",
        __config__=ConfigDict(extra="forbid"),
        tags=(list[Literal[*tag_names]], Field(description="所选标签名")),
    )

    from app.services.llm import llm_chat_call

    system = (
        f"你是文本分类器，从以下标签中选出与文本匹配的 0 个或多个：{', '.join(tag_names)}。"
        "不相关的标签不要选。只输出所选标签列表。"
    )
    try:
        result = await llm_chat_call(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text_preview[:2000]},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "tag_selection",
                    "strict": True,
                    "schema": TagSelection.model_json_schema(),
                },
            },
        )
        selected = TagSelection.model_validate_json(result["content"]).tags
        return list(selected)
    except Exception:
        logger.warning("[infer-tags] failed, skipped", exc_info=True)
        return []


# ── 文档入库（异步） ───────────────────────────────────────────────


async def ingest_document(
    file_path: str, filename: str, *, tags: list[str] | None = None,
) -> dict[str, Any]:
    """创建文档记录并入队异步解析。同名预检由 router 处理。"""
    suffix = os.path.splitext(filename)[1].lower()
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None

    async with database.AsyncSessionLocal() as session:
        doc = DocumentRecord(
            filename=filename,
            file_path=file_path,
            file_size=file_size,
            file_ext=suffix,
            status=DocumentStatus.PENDING,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        doc_id = doc.id

        if tags:
            rows = (await session.exec(select(TagRecord).where(TagRecord.name.in_(tags)))).all()
            by_name = {t.name: t.id for t in rows}
            for name in tags:
                if name in by_name:
                    session.add(DocumentTagRecord(document_id=doc_id, tag_id=by_name[name]))
            await session.commit()

    from app.utils.arq import enqueue_parse
    await enqueue_parse(doc_id)

    logger.info("[kb] document %d enqueued for parsing: %s", doc_id, filename)
    return {"doc_id": doc_id, "status": DocumentStatus.PENDING}


async def ingest_document_sync(stored_path: str, filename: str) -> int:
    """同步入库：创建文档 → 解析 → 写入切片，返回 document_id。

    用于 agent 对话上传文件，需要立即可用的场景。
    """
    import asyncio
    import json
    import sys
    import tempfile

    suffix = os.path.splitext(filename)[1].lower()
    file_path = str(settings.UPLOADS_DIR / Path(stored_path).name)
    file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None

    async with database.AsyncSessionLocal() as session:
        doc = DocumentRecord(
            filename=filename,
            file_path=file_path,
            file_size=file_size,
            file_ext=suffix,
            status=DocumentStatus.PROCESSING,
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        doc_id = doc.id

    try:
        output_fd, output_path = tempfile.mkstemp(suffix=".json")
        os.close(output_fd)

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.services.docling_chunk", file_path,
            "--output", output_path,
            "--doc-id", str(doc_id),
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
            await _update_document_status(doc_id, DocumentStatus.FAILED, error="未解析出文本块")
            raise RuntimeError("未解析出文本块")

        inserted = await _insert_chunks(chunks, document_id=doc_id)
        if inserted == 0:
            await _update_document_status(doc_id, DocumentStatus.FAILED, error="文本块入库失败")
            raise RuntimeError("文本块入库失败")

        await _update_document_status(doc_id, DocumentStatus.COMPLETED, chunk_count=inserted)
        logger.info("[kb] sync ingest doc %d completed: %d chunks", doc_id, inserted)
        return doc_id

    except Exception as exc:
        await _update_document_status(doc_id, DocumentStatus.FAILED, error=str(exc)[:2000])
        raise


# ── 文档管理 ────────────────────────────────────────────────────────


async def list_all_documents(
    limit: int = 50,
    offset: int = 0,
    status: str = "",
    q: str = "",
    tags: list[str] | None = None,
) -> tuple[list[DocumentListItem], int]:
    """返回所有文档（分页 + 状态/文件名/标签筛选）。"""
    async with database.AsyncSessionLocal() as session:
        base = select(DocumentRecord)

        if status:
            base = base.where(DocumentRecord.status == status)
        if q:
            like = f"%{q}%"
            base = base.where(DocumentRecord.filename.ilike(like))
        if tags:
            tagged = (
                select(DocumentTagRecord.document_id)
                .join(TagRecord, DocumentTagRecord.tag_id == TagRecord.id)
                .where(TagRecord.name.in_(tags))
            )
            base = base.where(DocumentRecord.id.in_(tagged))

        total = (await session.execute(
            select(func.count()).select_from(base.subquery())
        )).scalar() or 0
        rows = (await session.execute(
            base.order_by(DocumentRecord.id.desc()).offset(offset).limit(limit)
        )).scalars().all()

    items = [
        DocumentListItem(
            id=d.id, filename=d.filename, status=d.status,
            chunk_count=d.chunk_count, error=d.error,
            parse_duration_ms=d.parse_duration_ms, file_size=d.file_size,
            created_at=d.created_at.isoformat() if d.created_at else None,
            tags=[t.name for t in d.tags],
        )
        for d in rows
    ]
    return items, total


async def get_document(doc_id: int) -> dict[str, Any] | None:
    async with database.AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if doc is None:
            return None
        return {
            "id": doc.id, "filename": doc.filename,
            "file_path": doc.file_path,
            "file_size": doc.file_size, "file_ext": doc.file_ext,
            "status": doc.status, "chunk_count": doc.chunk_count,
            "error": doc.error,
            "parse_duration_ms": doc.parse_duration_ms,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
            "tags": [t.name for t in doc.tags],
        }


async def delete_document(doc_id: int) -> bool:
    """删除文档：排队任务、切片、标签、源文件、解析产物一并清理。"""
    from app.utils.arq import cancel_parse as _cancel_parse
    try:
        await _cancel_parse(doc_id)
    except Exception:
        logger.warning("[kb] cancel_parse(%d) failed, continue delete", doc_id, exc_info=True)

    async with database.AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if not doc:
            return False
        file_path = doc.file_path
        await session.execute(delete(ChunkRecord).where(ChunkRecord.document_id == doc_id))
        await session.execute(delete(DocumentTagRecord).where(DocumentTagRecord.document_id == doc_id))
        await session.delete(doc)
        await session.commit()

    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("[kb] unlink(%s) failed", file_path, exc_info=True)
    try:
        Path("parsed", f"{doc_id}.json").unlink(missing_ok=True)
    except OSError:
        logger.warning("[kb] unlink(parsed/%d.json) failed", doc_id, exc_info=True)

    return True


async def update_document_tags(doc_id: int, tags: list[str]) -> bool:
    """替换文档的标签关联。"""
    async with database.AsyncSessionLocal() as session:
        doc = await session.get(DocumentRecord, doc_id)
        if not doc:
            return False
        await session.execute(delete(DocumentTagRecord).where(DocumentTagRecord.document_id == doc_id))
        rows = (await session.exec(select(TagRecord).where(TagRecord.name.in_(tags)))).all()
        for tag in rows:
            session.add(DocumentTagRecord(document_id=doc_id, tag_id=tag.id))
        await session.commit()
    return True


async def get_document_status_counts() -> dict[str, int]:
    """按状态统计文档数量。"""
    async with database.AsyncSessionLocal() as session:
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
) -> tuple[list[ChunkItem], int]:
    """列出文档的切片（分页 + 可选搜索）。"""
    async with database.AsyncSessionLocal() as session:
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
        ChunkItem(
            id=c.id, document_id=c.document_id, file_name=c.file_name,
            page_numbers=c.page_numbers or [],
            heading_context=c.heading_context or "",
            raw_content=c.raw_content or "",
        )
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
    async with database.AsyncSessionLocal() as session:
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

    async with database.AsyncSessionLocal() as session:
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
                continue
        await delete_job(doc_job_id(doc.id))
        if await enqueue_parse(doc.id):
            logger.info("reconcile: re-enqueued orphan doc %d", doc.id)