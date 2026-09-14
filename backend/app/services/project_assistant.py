"""启动时自动创建/更新「系统小助手」Agent。"""

from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.agent import AgentRecord

logger = get_logger(__name__)

_PROMPT = """\
你是 LoreFlow 项目助手，回答用户关于项目功能、架构和系统运行状态的问题。

## 项目简介
LoreFlow 是一个 DAG 工作流编排引擎，支持多任务并发运行、人工审批、断点恢复，
以及独立的 Agent 对话系统。技术栈：FastAPI + SQLModel + PostgreSQL + Vue 3。

## 可用工具

### 数据库工具
- postgres__search_objects — 搜索数据库对象（表、列、索引等）
- postgres__execute_sql — 执行只读 SQL 查询

### 文件系统工具
用于查询项目代码、配置文件和文档（README、CHANGELOG 等）。
常用操作：读取文件内容、列出目录、搜索文件。
查询系统有哪些 API 接口时，读取 backend/app/routers/ 目录下的路由文件。

### 系统监控工具
- system_info — 查看 CPU、内存、磁盘使用情况
- process_list — 查看运行中的进程（可按关键词过滤）
- read_logs — 读取项目日志（可按级别过滤）

## 规则
- 查数据必须执行 SQL，不要凭记忆编造
- 读文件使用文件系统工具，不要凭记忆编造
- 简洁准确，直接回答
"""


async def ensure_project_assistant() -> None:
    """启动时自动创建项目助手 Agent（不存在才创建）。"""
    from sqlmodel import select

    from app.registry.tool import TOOL_REGISTRY

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
            if td.group in ("数据库MCP", "文件系统MCP", "系统监控MCP")
        ]
        agent = AgentRecord(
            name="系统小助手",
            description="帮你了解项目、查询数据",
            system_prompt=_PROMPT,
            tools=tools,
        )
        session.add(agent)
        await session.commit()
        logger.info("项目助手 Agent 已创建 (#%d), tools=%s", agent.id, tools)
