"""
Database layer — PostgreSQL via SQLModel (asyncpg driver).

Session-per-call helpers for run snapshots and review decisions.
``AsyncSessionLocal`` 是模块级全局、调用时才查找——测试 conftest
直接赋值为 aiosqlite sessionmaker 即可换库，无需 DI/monkeypatch。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.run import ReviewDecision, RunRecord

engine = create_async_engine(str(settings.DATABASE_URL))
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def save(record: RunRecord) -> None:
    """把记录同步到库（原地，不换对象）。

    首次插入（id is None）走 add：自增 id 回填到同一对象，这样提前
    捕获了该对象的闭包（如 approver/sink）也能看到 id；之后走 merge。
    """
    async with AsyncSessionLocal() as session:
        if record.id is None:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return
        await session.merge(record)
        await session.commit()


async def load() -> dict[int, RunRecord]:
    """All persisted run records, keyed by run id."""
    async with AsyncSessionLocal() as session:
        records = (await session.exec(select(RunRecord))).all()
    return {rec.id: rec for rec in records if rec.id is not None}


async def get(run_id: int) -> RunRecord | None:
    """One run record, or ``None``."""
    async with AsyncSessionLocal() as session:
        return await session.get(RunRecord, run_id)


async def save_decision(run_id: int, node_name: str, decision: dict[str, Any]) -> None:
    """写入一条审批决策（浏览器答复）。"""
    row = ReviewDecision(
        run_id=run_id,
        node_name=node_name,
        approve=decision["approve"],
        reason=decision.get("reason"),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()


async def take_decision(run_id: int, node_name: str) -> dict[str, Any] | None:
    """取走并删除该节点最近一条决策（审批器轮询消费）。"""
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(ReviewDecision)
            .where(ReviewDecision.run_id == run_id, ReviewDecision.node_name == node_name)
            .order_by(cast(Any, ReviewDecision.id).desc())
        )
        row = result.first()
        if row is None:
            return None
        decision = {"approve": row.approve, "reason": row.reason}
        await session.delete(row)
        await session.commit()
        return decision
