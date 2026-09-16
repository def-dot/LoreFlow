"""Agent 节点：子 Agent 执行"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.registry.types import func

logger = logging.getLogger(__name__)


class AgentRunParams(BaseModel):
    prompt: str = Field(description="发送给 Agent 的提示", min_length=1)
    sessions: list[str] | None = Field(default=None, description="前置会话 ID，用于记忆关联")
    tools: list[str] | None = Field(default=None, description="仅激活的工具名称列表")


class AgentRunOutput(BaseModel):
    text: str = Field(description="Agent 最终回复")
    conversation_id: str = Field(description="本次 Agent 运行会话 ID")


@func(
    label="子 Agent",
    description="创建子 Agent 会话并执行指令（支持关联上下文）",
    metadata={"group": "Agent"},
)
async def agent_run(params: AgentRunParams) -> AgentRunOutput:
    from app.services.agents import AgentService
    from app.database import AsyncSessionFactory

    conversation_id = ""
    text = ""

    async with AsyncSessionFactory() as session:
        svc = AgentService(session)
        conversation_id, tool_use_events, text = await svc.create_and_run(
            agent_id=0,
            prompt=params.prompt,
            sessions=params.sessions or [],
            tools=set(params.tools) if params.tools else None,
        )
        await session.commit()

    return AgentRunOutput(text=text, conversation_id=conversation_id)
