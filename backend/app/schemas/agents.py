"""Agent / 对话 相关请求/响应 schema。"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.agent import MessageRecord


# ── Agent ────────────────────────────────────────────────────────────────────

class _AgentBody(BaseModel):
    """Agent 请求体公共字段。"""

    name: str = Field(max_length=200)
    description: str = ""
    system_prompt: str = ""
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class AgentCreate(_AgentBody):
    """创建 Agent 请求体。"""


class AgentUpdate(_AgentBody):
    """更新 Agent 请求体。"""


# ── Conversation / Message ───────────────────────────────────────────────────

class ChatRequest(BaseModel):
    """发送聊天消息请求体。"""

    message: str
    file_ids: list[int] = Field(default_factory=list)


class ConversationCreate(BaseModel):
    """创建对话请求体。"""

    agent_id: int
    title: str = ""


class ConversationDetail(BaseModel):
    """对话详情，包含消息列表。"""

    id: int
    agent_id: int
    title: str = ""
    created_at: datetime
    updated_at: datetime | None = None
    messages: list[MessageRecord] = Field(default_factory=list)
