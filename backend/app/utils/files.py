"""上传文件的磁盘存取与文本解码 — UTF-8 优先、GBK 兜底；PDF 用 pypdf 提取。

settings 在函数内动态读取（不在 import 时固化），测试可
``monkeypatch.setattr(settings, "UPLOADS_DIR", tmp_path)`` 重定向。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import settings

ALLOWED_SUFFIXES = frozenset({".txt", ".md", ".pdf"})


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


def _extract_pdf_text(data: bytes) -> str:
    """从 PDF 二进制内容提取纯文本（逐页拼接）。"""
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def save_upload(data: bytes, suffix: str) -> str:
    """惰性建目录并落盘，返回相对路径 ``uploads/{uuid_hex}{suffix}``。"""
    settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    (settings.UPLOADS_DIR / stored_name).write_bytes(data)
    return f"uploads/{stored_name}"


def read_upload(path_or_name: str) -> str:
    """按相对路径或文件名读全文文本（防路径穿越与白名单外扩展名）。

    接受 ``uploads/{uuid}.ext`` 或 ``{uuid}.ext`` 两种格式。
    PDF 文件使用 pypdf 提取文本，其余走 decode_text 编码探测链。
    """
    if not path_or_name or ".." in path_or_name or "\\" in path_or_name:
        raise ValueError("无效的文件引用")
    # 兼容两种格式：uploads/abc.txt 或 abc.txt
    name = path_or_name.split("/", 1)[1] if "/" in path_or_name else path_or_name
    if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError("无效的文件引用：不支持的文件类型")
    path = settings.UPLOADS_DIR / name
    if not path.is_file():
        raise ValueError(f"上传文件不存在或已被清理：{path_or_name}")
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text(path.read_bytes())
    return decode_text(path.read_bytes())
