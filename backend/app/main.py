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

from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.routers import health, node_types, pipelines, runs
from app.services import orchestrator

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await orchestrator.resume_stuck_runs()
    yield


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
app.include_router(health.router, prefix=API_V1)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
