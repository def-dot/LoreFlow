"""知识库管理 API。"""

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.schemas.knowledge import ChunkListResponse, DocumentListResponse, StatusCounts
from app.services import knowledge
from app.utils import files

# ── 文档（扁平接口）──────────────────────────────────────────────────

router = APIRouter(route_class=UnifiedResponseRoute, tags=["knowledge"])


@router.post("/documents/upload", status_code=202)
async def upload_document_direct(
    file: UploadFile = File(..., description="文档文件（.txt/.md/.pdf）"),
    kb_id: int | None = Form(None, description="知识库 ID，留空则归入默认知识库"),
) -> dict:
    """直接上传文档，无需预先指定知识库。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in files.ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的文件类型 {suffix or '（无扩展名）'}")
    data = await file.read()
    if not data:
        raise ValueError("上传的文件内容为空")
    if len(data) > settings.UPLOAD_MAX_MB * 1024 * 1024:
        raise ValueError(f"文件超过大小上限（{settings.UPLOAD_MAX_MB}MB）")

    if kb_id is None:
        kb_id = await knowledge.get_or_create_default_kb()

    stored = files.save_upload(data, suffix)
    result = await knowledge.ingest_document(kb_id, stored, filename)
    return {"doc_id": result["doc_id"], "filename": filename, "status": result["status"]}


@router.get("/documents")
async def list_all_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query("", description="按状态筛选"),
) -> DocumentListResponse:
    items, total = await knowledge.list_all_documents(limit=limit, offset=offset, status=status)
    return DocumentListResponse(items=items, total=total)


@router.get("/documents/status")
async def get_document_status() -> StatusCounts:
    counts = await knowledge.get_document_status_counts()
    return StatusCounts(**counts)


@router.get("/documents/{doc_id}")
async def get_document(doc_id: int) -> dict:
    doc = await knowledge.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.get("/documents/{doc_id}/chunks")
async def list_document_chunks(
    doc_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    q: str = Query("", description="搜索关键词"),
) -> ChunkListResponse:
    items, total = await knowledge.list_chunks(doc_id, page=page, page_size=page_size, q=q)
    return ChunkListResponse(items=items, total=total)


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int) -> dict:
    if not await knowledge.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"deleted": doc_id}


@router.post("/documents/{doc_id}/cancel")
async def cancel_document(doc_id: int) -> dict:
    ok = await knowledge.cancel_document(doc_id)
    if not ok:
        raise HTTPException(status_code=400, detail="该文档当前无法取消")
    return {"document_id": doc_id, "cancelled": True}


@router.post("/documents/{doc_id}/retry")
async def retry_document(doc_id: int) -> dict:
    ok = await knowledge.retry_document(doc_id)
    if not ok:
        raise HTTPException(status_code=400, detail="该文档当前无法重试")
    return {"document_id": doc_id, "retried": True}


# ── 知识库 CRUD ──────────────────────────────────────────────────────

kb_router = APIRouter(prefix="/knowledge-bases", route_class=UnifiedResponseRoute, tags=["knowledge"])


class KBCreate(BaseModel):
    name: str
    description: str = ""


@kb_router.post("", status_code=201)
async def create_kb(body: KBCreate) -> dict:
    return await knowledge.create_kb(body.name, body.description)


@kb_router.get("")
async def list_kbs() -> list[dict]:
    return await knowledge.list_kbs()


@kb_router.delete("/{kb_id}")
async def delete_kb(kb_id: int) -> dict:
    if not await knowledge.delete_kb(kb_id):
        raise HTTPException(status_code=404, detail="知识库不存在")
    return {"deleted": kb_id}


@kb_router.get("/{kb_id}/documents")
async def list_documents(kb_id: int) -> list[dict]:
    return await knowledge.list_documents(kb_id)


@kb_router.post("/{kb_id}/documents/upload", status_code=202)
async def upload_and_ingest(
    kb_id: int,
    file: UploadFile = File(..., description="文档文件（.txt/.md/.pdf）"),
) -> dict:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in files.ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的文件类型 {suffix or '（无扩展名）'}")
    data = await file.read()
    if not data:
        raise ValueError("上传的文件内容为空")
    if len(data) > settings.UPLOAD_MAX_MB * 1024 * 1024:
        raise ValueError(f"文件超过大小上限（{settings.UPLOAD_MAX_MB}MB）")

    stored = files.save_upload(data, suffix)
    result = await knowledge.ingest_document(kb_id, stored, filename)
    return {"doc_id": result["doc_id"], "filename": filename, "status": result["status"]}