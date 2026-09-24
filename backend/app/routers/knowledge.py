"""知识库管理 API。"""

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.services import knowledge
from app.utils import files

# ── 文档（扁平接口，不区分知识库）──────────────────────────────────────────

router = APIRouter(route_class=UnifiedResponseRoute, tags=["knowledge"])


@router.get("/documents")
async def list_all_documents() -> list[dict]:
    return await knowledge.list_all_documents()


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: int) -> dict:
    if not await knowledge.delete_document(doc_id):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"deleted": doc_id}


# ── 知识库 CRUD ──────────────────────────────────────────────────────────

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


@kb_router.post("/{kb_id}/documents/upload", status_code=201)
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
    return {"doc_id": result["doc_id"], "filename": filename, "chunk_count": result["chunk_count"]}
