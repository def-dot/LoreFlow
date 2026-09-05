"""Database models — run records."""

from enum import Enum
from typing import Any

from sqlalchemy import JSON, Column, Text
from sqlalchemy import Enum as SAEnum
from sqlmodel import Field, SQLModel


class RunStatus(Enum):
    """Run 生命周期状态（值即库里存的字符串，与前端/接口口径一致）。"""

    PENDING = "pending"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunRecord(SQLModel, table=True):
    """One persisted run snapshot (nodes stored as JSON)."""

    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)  # 自增
    name: str = ""  # 任务名称（用户自定义或配置文件名）
    pipeline: str = ""  # 工作流名称（YAML 的 name 字段）
    created_at: str | None = None
    finished_at: str | None = None
    # native_enum=False + values_callable：沿用 VARCHAR 列按 value 存取，兼容存量库
    status: RunStatus = Field(
        default=RunStatus.PENDING,
        sa_column=Column(
            SAEnum(RunStatus, native_enum=False, values_callable=lambda cls: [e.value for e in cls])
        ),
    )
    error: str | None = None
    nodes: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    inputs: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # 运行时输入快照（resume 回放用）
    output: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    definition: str | None = Field(default=None, sa_column=Column(Text))
