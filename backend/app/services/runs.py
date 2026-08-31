"""Run 持久化 — RunRecord 的仓储操作。

只做数据访问，执行编排见 services/orchestrator.py。持久化函数调用时
才查找 ``database.AsyncSessionLocal``——conftest 直接换掉该全局即可换库。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlmodel import func, select

from app.core import database
from app.models.run import RunRecord, RunStatus


async def create(record: RunRecord) -> None:
    """插入新 run 记录（id 由 DB 生成回填）。
    """
    async with database.AsyncSessionLocal() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)


async def list_runs(
    offset: int = 0,
    limit: int | None = None,
    status: RunStatus | None = None,
    config_file: str | None = None,
) -> tuple[list[RunRecord], int]:
    """分页取 run（新在前），支持按状态/流水线筛选；total 为**筛选后**总数。

    ``limit=None`` 表示不限条数——启动时 resume_stuck_runs 需要扫全部记录。
    """
    # 筛选条件列表/计数共用，保证两者口径一致
    cond = []
    if status is not None:
        cond.append(RunRecord.status == status)
    if config_file is not None:
        cond.append(RunRecord.config_file == config_file)

    async with database.AsyncSessionLocal() as session:
        stmt = select(RunRecord)
        count_stmt = select(func.count(RunRecord.id))
        if cond:
            stmt = stmt.where(*cond)
            count_stmt = count_stmt.where(*cond)
        stmt = stmt.order_by(RunRecord.created_at.desc(), RunRecord.id.desc()).offset(offset).limit(limit)
        rows = list((await session.exec(stmt)).all())
        total = (await session.exec(count_stmt)).one()
        return rows, total


# run 终态集合：与前端 TERMINAL_STATUSES 对应
TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


async def run_counts() -> dict[str, int]:
    """全局执行计数（**不受筛选影响**）：running 驱动前端轮询启停，
    active（含 reviewing 等非终态）驱动导航“电流”等页面状态。"""
    async with database.AsyncSessionLocal() as session:
        running = (
            await session.exec(
                select(func.count(RunRecord.id)).where(RunRecord.status == RunStatus.RUNNING)
            )
        ).one()
        active = (
            await session.exec(
                select(func.count(RunRecord.id)).where(RunRecord.status.not_in(TERMINAL_STATUSES))
            )
        ).one()
        return {"running": running, "active": active}


async def get_run(run_id: int) -> RunRecord | None:
    """One run record, or ``None``."""
    async with database.AsyncSessionLocal() as session:
        return await session.get(RunRecord, run_id)


async def save_nodes(record: RunRecord) -> None:
    """节点快照落库（事件汇与 approve 决策写入共用的唯一持久化路径）。

    决策随快照持久化：approve 后进程崩溃，启动恢复重跑时 approver 仍能
    从快照取到决策。
    """
    async with database.AsyncSessionLocal() as session:
        await session.execute(
            update(RunRecord).where(RunRecord.id == record.id).values(nodes=record.nodes)
        )
        await session.commit()


async def delete_run(run_id: int) -> bool:
    """删除一条**终态** run（决策在节点快照里，随 run 一并清理）。

    不存在或非终态返回 False（由路由层区分 404/400）。
    """
    async with database.AsyncSessionLocal() as session:
        record = await session.get(RunRecord, run_id)
        if record is None or record.status not in TERMINAL_STATUSES:
            return False
        await session.delete(record)
        await session.commit()
        return True
