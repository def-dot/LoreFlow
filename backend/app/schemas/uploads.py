"""Pydantic 响应模型 — /api/v1/uploads"""

from pydantic import BaseModel


class UploadOut(BaseModel):
    """一次成功上传的文件引用（file 参数的值 = 其中的 {id, filename}）。"""

    id: str  # 存储文件名（uuid+扩展名）：run 时按它读盘
    filename: str  # 原始文件名（展示 / doc_id / title 来源）
    size: int  # 字节数
