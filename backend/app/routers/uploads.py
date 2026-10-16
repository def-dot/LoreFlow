"""文件上传 — /api/v1/uploads（选文件即上传，返回 store_path 相对路径供 run 参数引用）"""

from fastapi import APIRouter, File, UploadFile

from app.core.response import UnifiedResponseRoute
from app.models.upload import UploadRecord
from app.schemas.agents import FileRef
from app.services import uploads as uploads_service
from app.utils.files import check_upload, save_upload

router = APIRouter(prefix="/uploads", route_class=UnifiedResponseRoute, tags=["uploads"])


@router.post("", response_model=FileRef, status_code=201)
async def upload_file(
    file: UploadFile = File(..., description="文本文件（.txt/.md/.pdf）"),
) -> UploadRecord:
    filename = file.filename or ""
    data = await file.read()
    suffix = check_upload(filename, data)
    store_path = save_upload(data, suffix)  # uploads/{uuid}.ext
    record = UploadRecord(store_path=store_path, filename=filename, size=len(data))
    await uploads_service.create(record)
    return FileRef(
        store_path=store_path,
        filename=filename,
    )