"""Upload 持久化 — UploadRecord 的仓储操作。"""

from __future__ import annotations

from app.core import database
from app.models.upload import UploadRecord


async def create(record: UploadRecord) -> None:
    """插入新上传记录（id 由调用方生成，即 UUID 文件名）。"""
    async with database.AsyncSessionLocal() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)


