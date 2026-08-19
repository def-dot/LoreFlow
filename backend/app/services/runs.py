"""Run 持久化 — RunRecord 的仓储操作。

与 services/reviews.py 对称：只做数据访问，执行编排见
services/orchestrator.py。持久化函数调用时才查找
``database.AsyncSessionLocal``——conftest 直接换掉该全局即可换库。
"""

from __future__ import annotations

from sqlmodel import func, select

from app.core import database
from app.models.run import RunRecord


async def save(record: RunRecord) -> None:
    """把记录同步到库。
    """
    async with database.AsyncSessionLocal() as session:
        if record.id is None:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return
        await session.merge(record)
        await session.commit()


async def list_runs(
    offset: int = 0, limit: int | None = None
) -> tuple[list[RunRecord], int]:
    """分页取全部持久化 run（新在前），连同全局总数一起返回。

    ``limit=None`` 表示不限条数——启动时 resume_stuck_runs 需要扫全部记录。
    """
    async with database.AsyncSessionLocal() as session:
        stmt = (
            select(RunRecord)
            .order_by(RunRecord.created_at.desc(), RunRecord.id.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = list((await session.exec(stmt)).all())
        total = (await session.exec(select(func.count(RunRecord.id)))).one()
        return rows, total


async def get_run(run_id: int) -> RunRecord | None:
    """One run record, or ``None``."""
    async with database.AsyncSessionLocal() as session:
        return await session.get(RunRecord, run_id)
