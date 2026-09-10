"""Database models — independent agent entities."""

from datetime import datetime
from typing import Any

from pydantic import ConfigDict
from sqlalchemy import JSON, Column, Text
from sqlmodel import Field, SQLModel


class AgentRecord(SQLModel, table=True):
    """可独立对话的 Agent 配置。"""

    __tablename__ = "agents"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(max_length=200)
    description: str = ""
    system_prompt: str = Field(default="", sa_column=Column(Text))
    model: str = ""  # provider:model 格式，空则用全局默认
    tools: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    skills: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    max_iterations: int = 5
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime | None = None


class ConversationRecord(SQLModel, table=True):
    """一次对话会话。"""

    __tablename__ = "conversations"

    id: int | None = Field(default=None, primary_key=True)
    agent_id: int = Field(index=True)
    title: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime | None = None


class MessageRecord(SQLModel, table=True):
    """对话中的一条消息。"""

    __tablename__ = "messages"

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True)
    role: str = Field(max_length=20)  # user / assistant / tool
    content: str = Field(default="", sa_column=Column(Text))
    tool_calls: dict[str, Any] | list[Any] | None = Field(default=None, sa_column=Column(JSON))
    tool_call_id: str | None = None
    tool_name: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
