"""共享 fixtures — aiosqlite 内存库 + ASGITransport 客户端。

ASGITransport 不运行 lifespan（应用不会自动建表/恢复 run），所以
建表/清表由 autouse fixture 负责；数据库函数在调用时才查找模块级
``AsyncSessionLocal``，直接赋值换成 SQLite sessionmaker 即可换库。
"""

from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

import app.core.database as db_mod
from app.main import app

TEST_DATABASE_URL = "sqlite+aiosqlite://"
test_engine = create_async_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

# 换库：数据库层所有函数经此全局取 session
db_mod.AsyncSessionLocal = TestSessionLocal


@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """每个测试前建表，测试后清表。"""
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(scope="session", autouse=True)
async def cleanup_engine() -> AsyncGenerator[None, None]:
    yield
    await test_engine.dispose()
