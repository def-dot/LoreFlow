"""Database models — upload records."""

from datetime import datetime

from sqlmodel import Field, SQLModel


class UploadRecord(SQLModel, table=True):
    """上传文件的元数据记录（文件本体存磁盘，元数据存DB）。"""

    __tablename__ = "uploads"

    id: str = Field(primary_key=True)  # UUID文件名，如 "a1b2c3d4.txt"
    filename: str  # 原始文件名，如 "我的文档.txt"
    size: int  # 文件大小（字节）
    created_at: datetime = Field(default_factory=datetime.now)
