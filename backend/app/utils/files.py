"""上传文件的磁盘存取。

settings 在函数内动态读取（不在 import 时固化），测试可
``monkeypatch.setattr(settings, "UPLOADS_DIR", tmp_path)`` 重定向。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings

ALLOWED_SUFFIXES = frozenset({".txt", ".md", ".pdf"})


def check_upload(filename: str, data: bytes) -> str:
    """校验上传文件（后缀白名单/非空/大小上限），返回小写后缀；不通过抛 ValueError。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"不支持的文件类型 {suffix or '（无扩展名）'}：仅支持 .txt/.md/.pdf 文件")
    if not data:
        raise ValueError("上传的文件内容为空")
    if len(data) > settings.UPLOAD_MAX_MB * 1024 * 1024:
        raise ValueError(f"文件超过大小上限（{settings.UPLOAD_MAX_MB}MB）")
    return suffix


def save_upload(data: bytes, suffix: str) -> str:
    """惰性建目录并落盘，返回相对路径 ``uploads/{uuid_hex}{suffix}``。"""
    settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    (settings.UPLOADS_DIR / stored_name).write_bytes(data)
    return f"uploads/{stored_name}"
