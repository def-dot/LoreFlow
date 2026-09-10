"""Pydantic 请求/响应模型 — /api/v1/agents & /api/v1/conversations 系列"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class ConversationCreate(BaseModel):
    agent_id: int
    title: str = ""


class ConversationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    agent_id: int
    title: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_serializer("created_at", "updated_at")
    def _format_dt(self, value: datetime | None) -> str | None:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else value


class MessageItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    content: str = ""
    tool_calls: dict[str, Any] | list[Any] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    created_at: datetime | None = None

    @field_serializer("created_at")
    def _format_dt(self, value: datetime | None) -> str | None:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else value


class ConversationDetail(ConversationListItem):
    messages: list[MessageItem] = []


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    file_ids: list[str] = []  # 已上传文件的存储 ID 列表
