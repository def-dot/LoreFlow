"""
LoreFlow — DAG 工作流编排引擎的 Web 服务。

编排声明在 YAML（app/pipelines/pipeline.yaml）。服务支持多个并发 run、
执行历史持久化在 PostgreSQL（重启后恢复未完成的 run），人工审批
经由 REST API 完成。前端由 frontend/（Vue 3）托管：开发时 Vite
代理 /api，生产时 nginx 反代 /api 到本服务。

Run::

    uv run fastapi run app/main.py --reload
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.models.agent import AgentRecord
from app.registry.plugins import load_plugins
from app.registry.skills import discover_skills
from app.routers import (
    agents,
    conversations,
    health,
    knowledge,
    node_types,
    pipelines,
    plugins,
    registry,
    runs,
    uploads,
)
from app.services import orchestrator
from app.services.mcp_client import init_mcp, shutdown_mcp

setup_logging()
logger = get_logger(__name__)


_PROJECT_ASSISTANT_SYSTEM_PROMPT = """\
你是 LoreFlow 项目助手，回答用户关于项目功能、架构和系统运行状态的问题。

## 项目简介
LoreFlow 是一个 DAG 工作流编排引擎，支持多任务并发运行、人工审批、断点恢复，
以及独立的 Agent 对话系统。技术栈：FastAPI + SQLModel + PostgreSQL + Vue 3。

## 可用数据库工具
- postgres__search_objects — 搜索数据库对象（表、列、索引等）
- postgres__execute_sql — 执行只读 SQL 查询

## 工作方式
1. 先用 postgres__search_objects 了解表结构
2. 再用 postgres__execute_sql 执行 SQL 回答用户问题

## 规则
- 查数据必须执行 SQL，不要凭记忆编造
- 简洁准确，直接回答
"""


async def _ensure_project_assistant() -> None:
    """启动时自动创建项目助手 Agent（幂等）。"""
    from sqlmodel import select

    async with AsyncSessionLocal() as session:
        existing = (
            await session.exec(
                select(AgentRecord).where(AgentRecord.name == "__project_assistant__")
            )
        ).one_or_none()
        if existing:
            existing.system_prompt = _PROJECT_ASSISTANT_SYSTEM_PROMPT
            existing.description = "LoreFlow 项目助手 — 了解项目架构、查询系统数据"
            existing.tools = ["postgres"]
            existing.skills = []
            await session.commit()
            logger.info("项目助手 Agent 已更新 (#%d)", existing.id)
            return
        agent = AgentRecord(
            name="__project_assistant__",
            description="LoreFlow 项目助手 — 了解项目架构、查询系统数据",
            system_prompt=_PROJECT_ASSISTANT_SYSTEM_PROMPT,
            tools=["postgres"],
        )
        session.add(agent)
        await session.commit()
        logger.info("项目助手 Agent 已创建 (#%d)", agent.id)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    load_plugins()
    discover_skills(settings.SKILLS_DIR)
    try:
        await init_mcp(settings.MCP_CONFIG)
    except Exception:
        logger.exception("MCP 初始化失败")
    try:
        await _ensure_project_assistant()
    except Exception:
        logger.exception("项目助手 Agent 初始化失败")
    try:
        await orchestrator.resume_stuck_runs()
        yield
    finally:
        await shutdown_mcp()


app = FastAPI(
    title=settings.APP_NAME,
    docs_url="/docs" if settings.APP_ENV == "dev" else None,
    lifespan=lifespan,
)
register_exception_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_V1 = "/api/v1"
app.include_router(runs.router, prefix=API_V1)
app.include_router(node_types.router, prefix=API_V1)
app.include_router(pipelines.router, prefix=API_V1)
app.include_router(plugins.router, prefix=API_V1)
app.include_router(uploads.router, prefix=API_V1)
app.include_router(registry.router, prefix=API_V1)
app.include_router(agents.router, prefix=API_V1)
app.include_router(conversations.router, prefix=API_V1)
app.include_router(knowledge.router, prefix=API_V1)
app.include_router(knowledge.kb_router, prefix=API_V1)
app.include_router(health.router, prefix=API_V1)

# 静态文件：/uploads/*
app.mount("/uploads", StaticFiles(directory=settings.UPLOADS_DIR), name="uploads")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
