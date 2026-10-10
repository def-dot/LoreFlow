"""Database models — LLM provider / model / settings."""

from datetime import datetime
from typing import Optional

from pydantic import field_serializer
from sqlmodel import Field, Relationship, SQLModel


def mask_api_key(key: str) -> str:
    """API Key 脱敏：保留前 3 位与后 4 位，中间以 *** 代替；过短则整体 ***。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


class ProviderRecord(SQLModel, table=True):
    """LLM Provider 连接信息（如 Ollama、OpenAI、MiMo 等）。"""

    __tablename__ = "providers"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=100, unique=True)
    base_url: str = Field(max_length=500)
    api_key: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime | None = Field(default=None, sa_column_kwargs={"onupdate": datetime.now})

    models: list["ModelRecord"] = Relationship(back_populates="provider", sa_relationship_kwargs={"lazy": "selectin"},)

    @field_serializer("api_key")
    @staticmethod
    def _mask_key(key: str) -> str:
        return mask_api_key(key)


class ModelRecord(SQLModel, table=True):
    """模型清单条目，挂在某个 Provider 下。"""

    __tablename__ = "models"

    id: int | None = Field(default=None, primary_key=True)
    provider_id: int = Field(foreign_key="providers.id", index=True)
    name: str = Field(max_length=200)  # 如 "gpt-4"、"bge-m3"
    model_type: str = Field(default="chat", max_length=20)  # chat / embedding / rerank
    is_enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime | None = Field(default=None, sa_column_kwargs={"onupdate": datetime.now})

    provider: Optional["ProviderRecord"] = Relationship(
        back_populates="models",
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    @property
    def model_key(self) -> str:
        """provider_name/model_name，用于 settings 引用。"""
        return f"{self.provider.name}/{self.name}" if self.provider else self.name


class SettingRecord(SQLModel, table=True):
    """系统级 KV 配置（用途分配等）。"""

    __tablename__ = "settings"

    key: str = Field(primary_key=True, max_length=100)
    value: str = Field(default="")
    updated_at: datetime | None = Field(default=None, sa_column_kwargs={"onupdate": datetime.now})