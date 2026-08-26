"""文件上传 — /api/v1/uploads（选文件即上传，返回 {id, filename} 引用供 run 参数引用）"""

from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.schemas.uploads import UploadOut
from app.utils import files

router = APIRouter(prefix="/uploads", route_class=UnifiedResponseRoute, tags=["uploads"])


@router.post("", response_model=UploadOut, status_code=201)
async def upload_file(
    file: UploadFile = File(..., description="文本文件（.txt/.md/.markdown）"),
) -> UploadOut:
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in files.ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的文件类型 {suffix or '（无扩展名）'}：仅支持 .txt/.md/.markdown 文本文件")
    data = await file.read()
    if not data:
        raise ValueError("上传的文件内容为空")
    if len(data) > settings.UPLOAD_MAX_MB * 1024 * 1024:
        raise ValueError(f"文件超过大小上限（{settings.UPLOAD_MAX_MB}MB）")
    stored = files.save_upload(data, suffix)
    return UploadOut(id=stored, filename=filename, size=len(data))
