"""
Database layer — PostgreSQL via SQLModel (asyncpg driver).

只保留连接基础设施：``engine`` 与 ``AsyncSessionLocal``。run 域的
持久化操作见 ``app.services.runs``，审批决策见 ``app.services.reviews``。

``AsyncSessionLocal`` 是模块级全局、调用时才查找——测试 conftest
直接赋值为 aiosqlite sessionmaker 即可换库，无需 DI/monkeypatch。
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings

engine = create_async_engine(str(settings.DATABASE_URL))
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
