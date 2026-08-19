"""Run 持久化 — RunRecord 的仓储操作。

与 services/reviews.py 对称：只做数据访问，执行编排见
services/orchestrator.py。持久化函数调用时才查找
``database.AsyncSessionLocal``——conftest 直接换掉该全局即可换库。
"""

from __future__ import annotations

from sqlmodel import select

from app.core import database
from app.models.run import RunRecord


async def save(record: RunRecord) -> None:
    """把记录同步到库（原地，不换对象）。

    首次插入（id is None）走 add：自增 id 回填到同一对象，这样提前
    捕获了该对象的闭包（如 approver/sink）也能看到 id；之后走 merge。
    """
    async with database.AsyncSessionLocal() as session:
        if record.id is None:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return
        await session.merge(record)
        await session.commit()


async def list_runs() -> list[RunRecord]:
    """All persisted run records, newest first."""
    async with database.AsyncSessionLocal() as session:
        return list(
            (
                await session.exec(
                    select(RunRecord).order_by(RunRecord.created_at.desc())
                )
            ).all()
        )


async def get_run(run_id: int) -> RunRecord | None:
    """One run record, or ``None``."""
    async with database.AsyncSessionLocal() as session:
        return await session.get(RunRecord, run_id)
