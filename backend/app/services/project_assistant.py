"""启动时自动创建/更新「系统小助手」Agent。"""

from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.agent import AgentRecord

logger = get_logger(__name__)

_PROMPT = """\
你是 LoreFlow 项目助手，回答用户关于项目功能、架构和系统运行状态的问题。

## 规则
- 查询信息必须使用工具，不要凭记忆编造
- 简洁准确，直接回答
"""


async def ensure_project_assistant() -> None:
    from sqlmodel import select

    from app.registry.types import TOOL_REGISTRY

    async with AsyncSessionLocal() as session:
        existing = (
            await session.exec(
                select(AgentRecord).where(AgentRecord.name == "系统小助手")
            )
        ).one_or_none()
        if existing:
            return
        tools = [
            name for name, td in TOOL_REGISTRY.items()
            if td.metadata.get("group") in ("数据库MCP", "文件系统MCP", "系统监控MCP")
        ]
        agent = AgentRecord(
            name="系统小助手",
            description="帮你了解项目、查询数据、查看系统状态",
            system_prompt=_PROMPT,
            tools=tools,
        )
        session.add(agent)
        await session.commit()
        logger.info("项目助手 Agent 已创建 (#%d), tools=%s", agent.id, tools)
