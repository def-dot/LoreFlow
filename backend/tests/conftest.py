"""共享 fixtures — aiosqlite 内存库 + ASGITransport 客户端。

ASGITransport 不运行 lifespan（应用不会自动建表/恢复 run），所以
建表/清表由 autouse fixture 负责；数据库函数在调用时才查找模块级
``AsyncSessionLocal``，直接赋值换成 SQLite sessionmaker 即可换库。
"""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.core.database as db_mod
import app.services.orchestrator as orchestrator
from app.core.config import settings
from app.main import app
from app.registry.plugins import load_plugins

load_plugins()  # 模拟 lifespan 的插件加载（ASGITransport 不运行 lifespan）

TEST_DATABASE_URL = "sqlite+aiosqlite:///./test_db.sqlite"
test_engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

# 换库：数据库层所有函数经此全局取 session
db_mod.AsyncSessionLocal = TestSessionLocal

# SQLite 无 PG advisory lock：测试单进程无选主竞争，直接视为抢到锁。
async def _acquire_test_lock() -> bool:
    return True


async def _release_test_lock(_raw: bool) -> None:
    pass


orchestrator._acquire_recovery_lock = _acquire_test_lock
orchestrator._release_recovery_lock = _release_test_lock


@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """每个测试前建表，测试后清表。"""
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest.fixture(autouse=True)
def redirect_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上传目录指向临时目录：上传/rag_load 测试落盘不污染 backend/uploads/。"""
    monkeypatch.setattr(settings, "UPLOADS_DIR", tmp_path / "uploads")


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(scope="session", autouse=True)
async def cleanup_engine() -> AsyncGenerator[None, None]:
    yield
    await test_engine.dispose()
