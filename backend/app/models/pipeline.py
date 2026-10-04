"""Database models — pipeline records."""

from datetime import datetime

from sqlalchemy import Text, Column
from sqlmodel import Field, SQLModel


class PipelineRecord(SQLModel, table=True):
    """工作流定义的 DB 快照（YAML 文件同步入库）。"""

    __tablename__ = "pipelines"

    id: int = Field(primary_key=True)
    name: str = Field(unique=True, index=True)  # YAML name 字段
    description: str = ""  # YAML description 字段（冗余，列表展示用）
    agent_tool: bool = True  # 是否注册为 Agent 工具（YAML metadata.agent_tool 同步）
    definition: str = Field(sa_column=Column(Text))  # YAML 原文
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
