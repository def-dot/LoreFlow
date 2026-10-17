"""知识库管理 API。"""

import asyncio
from io import BytesIO
from pathlib import Path

import pypdfium2 as pdfium
from docling_core.types.doc import DoclingDocument  # type: ignore[attr-defined]
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, Field

from sqlmodel import select

from app.core import database
from app.core.response import UnifiedResponseRoute
from app.models.knowledge import DocumentRecord
from app.schemas.knowledge import ChunkListResponse, DocumentListResponse, StatusCounts, UploadResultItem
from app.services import knowledge

# ── 文档（扁平接口）──────────────────────────────────────────────────

router = APIRouter(route_class=UnifiedResponseRoute, tags=["knowledge"])


class UploadItem(BaseModel):
    store_path: str
    filename: str
    tags: list[str] = []


class UploadRequest(BaseModel):
    items: list[UploadItem] = Field(min_length=1)


class InferTagsRequest(BaseModel):
    store_paths: list[str]


@router.post("/documents/infer-tags", status_code=200)
async def infer_tags(body: InferTagsRequest) -> dict:
    """对已保存文件读文本预览并 LLM 推断标签，并行处理，逐文件返回推荐标签。"""

    async def _read_preview(store_path: str) -> tuple[str, str]:
        """读取文件文本预览，返回 (store_path, text_preview)。"""
        file_path = Path(store_path)
        suffix = file_path.suffix.lower()
        text = ""
        if suffix in (".txt", ".md"):
            text = file_path.read_text(encoding="utf-8", errors="replace")[:2000]
        elif suffix == ".pdf":
            try:
                pdf = pdfium.PdfDocument(str(file_path))
                text = "\n".join(
                    pdf[i].get_textpage().get_text_range() for i in range(min(len(pdf), 3))
                )[:2000]
            except Exception:
                text = ""
        return store_path, text

    previews = await asyncio.gather(*(_read_preview(s) for s in body.store_paths))

    # 并行 LLM 推断
    async def _infer(store_path: str, text: str) -> dict:
        if not text:
            return {"store_path": store_path, "suggested_tags": []}
        suggested = await knowledge.infer_tags_for_text(text)
        return {"store_path": store_path, "suggested_tags": suggested}

    results = await asyncio.gather(*(_infer(sn, txt) for sn, txt in previews))
    return {"items": list(results)}


@router.post("/documents/upload", status_code=202)
async def upload_documents(body: UploadRequest) -> list[UploadResultItem]:
    """批量入库（文件已在盘上）；同名冲突整批中止。"""
    # ── 预检查：同名冲突整批中止 ──
    filenames = [item.filename for item in body.items]
    async with database.AsyncSessionLocal() as session:
        rows = (await session.exec(
            select(DocumentRecord.filename).where(DocumentRecord.filename.in_(filenames))
        )).all()
        existing = set(rows)
    if existing:
        return [
            UploadResultItem(
                filename=item.filename,
                error=f"同名文档「{item.filename}」已存在" if item.filename in existing else None,
            )
            for item in body.items
        ]

    # ── 正常入库 ──
    items: list[UploadResultItem] = []
    for item in body.items:
        try:
            result = await knowledge.ingest_document(item.store_path, item.filename, tags=item.tags)
            items.append(UploadResultItem(filename=item.filename, doc_id=result["doc_id"], status=result["status"]))
        except Exception as exc:
            items.append(UploadResultItem(filename=item.filename, error=str(exc)))
    return items


@router.get("/documents")
async def list_all_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query("", description="按状态筛选"),
    q: str = Query("", description="按文件名搜索"),
    tags: str = Query("", description="按标签名筛选，逗号分隔"),
) -> DocumentListResponse:
    parsed_tags = [x.strip() for x in tags.split(",") if x.strip()] if tags else []
    items, total = await knowledge.list_all_documents(
        limit=limit, offset=offset, status=status, q=q, tags=parsed_tags
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
    tags: list[str]


@router.put("/documents/{doc_id}/tags")
async def update_document_tags(doc_id: int, body: UpdateDocTags) -> dict:
    if not await knowledge.update_document_tags(doc_id, body.tags):
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"document_id": doc_id, "tags": body.tags}


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


@router.post("/documents/{doc_id}/reparse")
async def reparse_document(doc_id: int) -> dict:
    ok = await knowledge.reparse_document(doc_id)
    if not ok:
        raise HTTPException(status_code=400, detail="该文档当前无法重新解析（仅支持已完成的文档）")
    return {"document_id": doc_id, "reparsed": True}


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
    async with database.AsyncSessionLocal() as db:
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
    try:
        return await knowledge.create_tag(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/tags")
async def list_tags() -> list[str]:
    return await knowledge.list_tags()


@router.put("/tags/{tag_name}")
async def update_tag(tag_name: str, body: TagCreate) -> dict:
    """重命名标签，文档上的引用由外键级联更新。"""
    try:
        tag = await knowledge.update_tag(tag_name, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not tag:
        raise HTTPException(status_code=404, detail="标签不存在")
    return tag


@router.delete("/tags/{tag_name}")
async def delete_tag(tag_name: str) -> dict:
    if not await knowledge.delete_tag(tag_name):
        raise HTTPException(status_code=404, detail="标签不存在")
    return {"deleted": tag_name}