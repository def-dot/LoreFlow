"""Agent 相关请求/响应 schema。"""

from pydantic import BaseModel, Field


class _AgentBody(BaseModel):
    """Agent 请求体公共字段。"""

    name: str = Field(max_length=200)
    description: str = ""
    system_prompt: str = ""
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    max_iterations: int = 5


class AgentCreate(_AgentBody):
    """创建 Agent 请求体。"""


class AgentUpdate(_AgentBody):
    """更新 Agent 请求体。"""
