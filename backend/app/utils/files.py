"""上传文件的磁盘存取与文本解码 — UTF-8 优先、GBK 兜底，无三方依赖。

settings 在函数内动态读取（不在 import 时固化），测试可
``monkeypatch.setattr(settings, "UPLOADS_DIR", tmp_path)`` 重定向。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings

ALLOWED_SUFFIXES = frozenset({".txt", ".md", ".markdown"})


def decode_text(data: bytes) -> str:
    """UTF-8 严格 → GBK 严格 → UTF-8 replace 保底（永不抛 UnicodeDecodeError）。

    UTF-8 必须在前：GBK 常能把 UTF-8 字节"成功"解成乱码，反之 valid
    UTF-8 不会被 UTF-8 先解失败。
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gbk")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def save_upload(data: bytes, suffix: str) -> str:
    """惰性建目录并落盘，返回存储名 ``{uuid_hex}{suffix}``（后端起的键，非用户输入）。"""
    settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{suffix}"
    (settings.UPLOADS_DIR / stored).write_bytes(data)
    return stored


def read_upload(upload_id: str) -> str:
    """按存储名读全文文本（JSON 模式可手输任意 id，先防路径穿越与白名单外扩展名）。"""
    if (
        not upload_id
        or "/" in upload_id
        or "\\" in upload_id
        or ".." in upload_id
        or Path(upload_id).suffix.lower() not in ALLOWED_SUFFIXES
    ):
        raise ValueError("无效的文件引用：document.id 必须是上传接口返回的文件标识")
    path = settings.UPLOADS_DIR / upload_id
    if not path.is_file():
        raise ValueError(f"上传文件不存在或已被清理：{upload_id}")
    return decode_text(path.read_bytes())
