"""知识库管理 API。"""

from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from docling_core.types.doc import DoclingDocument  # type: ignore[attr-defined]
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.response import UnifiedResponseRoute
from app.models.knowledge import DocumentRecord, DocumentTagRecord, TagRecord
from app.schemas.knowledge import ChunkListResponse, DocumentListResponse, StatusCounts, TagInfo
from app.services import knowledge
from app.utils import files

# ── 文档（扁平接口）──────────────────────────────────────────────────

router = APIRouter(route_class=UnifiedResponseRoute, tags=["knowledge"])


@router.post("/documents/upload", status_code=202)
async def upload_document_direct(
    file: UploadFile = File(..., description="文档文件（.txt/.md/.pdf）"),
    tag_ids: str = Form("", description="标签 ID 列表，逗号分隔"),
) -> dict:
    """直接上传文档。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in files.ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的文件类型 {suffix or '（无扩展名）'}")
    data = await file.read()
    if not data:
        raise ValueError("上传的文件内容为空")
    if len(data) > settings.UPLOAD_MAX_MB * 1024 * 1024:
        raise ValueError(f"文件超过大小上限（{settings.UPLOAD_MAX_MB}MB）")

    parsed_tag_ids = [int(x) for x in tag_ids.split(",") if x.strip()] if tag_ids else []
    stored = files.save_upload(data, suffix)
    result = await knowledge.ingest_document(stored, filename, tag_ids=parsed_tag_ids)
    return {"doc_id": result["doc_id"], "filename": filename, "status": result["status"]}


@router.get("/documents")
async def list_all_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query("", description="按状态筛选"),
    q: str = Query("", description="按文件名搜索"),
    tag_ids: str = Query("", description="按标签筛选，逗号分隔"),
) -> DocumentListResponse:
    parsed_tag_ids = [int(x) for x in tag_ids.split(",") if x.strip()] if tag_ids else []
    items, total = await knowledge.list_all_documents(
        limit=limit, offset=offset, status=status, q=q, tag_ids=parsed_tag_ids
    )
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


class UpdateDocTags(BaseModel):
    tag_ids: list[int]


@router.put("/documents/{doc_id}/tags")
async def update_document_tags(doc_id: int, body: UpdateDocTags) -> dict:
    if not await knowledge.update_document_tags(doc_id, body.tag_ids):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"document_id": doc_id, "tag_ids": body.tag_ids}


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


@router.get("/documents/{doc_id}/pages/{page_no}")
async def get_document_page(doc_id: int, page_no: int) -> dict:
    """获取文档单页的解析结果（Markdown + 元数据）"""
    json_path = Path("parsed") / f"{doc_id}.json"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="解析结果不存在")

    doc = DoclingDocument.load_from_json(json_path)
    total = len(doc.pages)

    # 非 PDF 文件可能没有分页，整体作为第 1 页
    if total == 0:
        markdown = doc.export_to_markdown()
        table_count = len(doc.tables)
        picture_count = len(doc.pictures)
        return {
            "page_no": 1,
            "total": 1,
            "markdown": markdown,
            "table_count": table_count,
            "picture_count": picture_count,
        }

    markdown = doc.export_to_markdown(page_no=page_no)
    table_count = sum(1 for item in doc.tables for prov in item.prov if prov.page_no == page_no)
    picture_count = sum(1 for item in doc.pictures for prov in item.prov if prov.page_no == page_no)

    return {
        "page_no": page_no,
        "total": total,
        "markdown": markdown,
        "table_count": table_count,
        "picture_count": picture_count,
    }


@router.get("/documents/{doc_id}/pages/{page_no}/image")
async def get_page_image(doc_id: int, page_no: int) -> Response:
    """渲染 PDF 单页为 PNG 图片，非 PDF 返回原文纯文本"""
    async with AsyncSessionLocal() as db:
        doc_record = await db.get(DocumentRecord, doc_id)
    if doc_record is None or not doc_record.file_path:
        raise HTTPException(status_code=404, detail="文档不存在")

    file_path = doc_record.file_path
    if not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="原始文件不存在")

    if not file_path.lower().endswith(".pdf"):
        try:
            text = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            raise HTTPException(status_code=500, detail="无法读取原文")
        return Response(content=text.encode("utf-8"), media_type="text/plain; charset=utf-8")

    pdf = pdfium.PdfDocument(file_path)
    page = pdf[page_no - 1]  # pypdfium2 0-indexed
    bitmap = page.render(scale=2.0)
    img = bitmap.to_pil().convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    page.close()
    pdf.close()
    return Response(content=buf.getvalue(), media_type="image/png")


# ── 标签 CRUD ────────────────────────────────────────────────────────


class TagCreate(BaseModel):
    name: str


@router.post("/tags", status_code=201)
async def create_tag(body: TagCreate) -> dict:
    tag = await knowledge.create_tag(body.name)
    return {"id": tag["id"], "name": tag["name"]}


@router.get("/tags")
async def list_tags() -> list[dict]:
    return await knowledge.list_tags()


@router.put("/tags/{tag_id}")
async def update_tag(tag_id: int, body: TagCreate) -> dict:
    tag = await knowledge.update_tag(tag_id, body.name)
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"id": tag["id"], "name": tag["name"]}


@router.delete("/tags/{tag_id}")
async def delete_tag(tag_id: int) -> dict:
    if not await knowledge.delete_tag(tag_id):
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"deleted": tag_id}