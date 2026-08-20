"""审批决策持久化 — ReviewDecision 的写入与认领。

决策走"浏览器写入、审批器续跑消费"的模式：/api/approve 落一条决策，
续跑（resume）的 approver 在挂起检查中 claim_decision 认领最近一条
未消费决策，认领后状态推进、继续执行。

行认领后保留（consumed_at 标记），作为审计痕迹：谁在何时批了什么。
未消费决策对 (run_id, node_name) 最多一条——create_decision 是 upsert，
重复提交（双击/并发）覆盖旧值，避免陈旧决策被该节点的下一次到达误消费。
持久化函数调用时才查找 ``database.AsyncSessionLocal``——conftest 直接
换掉该全局即可换库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import select

from app.core import database
from app.models.review import ReviewDecision


def _upsert_decision_stmt(
    run_id: int, node_name: str, decision: dict[str, Any], now: str
) -> Any:
    """INSERT..ON CONFLICT 覆盖同节点未消费决策（PG/SQLite 按引擎方言选构造）。

    冲突目标带 WHERE consumed_at IS NULL，与部分唯一索引一致：已消费的
    历史行不参与冲突判定，新提交照常插入新行（保留审计历史）。
    """
    insert_ctor = pg_insert if database.engine.dialect.name == "postgresql" else sqlite_insert
    return (
        insert_ctor(ReviewDecision)
        .values(
            run_id=run_id,
            node_name=node_name,
            approve=decision["approve"],
            reason=decision.get("reason"),
            created_at=now,
            consumed_at=None,
        )
        .on_conflict_do_update(
            index_elements=["run_id", "node_name"],
            index_where=ReviewDecision.consumed_at.is_(None),
            set_={
                "approve": decision["approve"],
                "reason": decision.get("reason"),
                "created_at": now,
            },
        )
    )


async def create_decision(run_id: int, node_name: str, decision: dict[str, Any]) -> None:
    """写入一条审批决策（浏览器答复）；已有未消费决策则覆盖（后答为准）。"""
    now = datetime.now().isoformat(timespec="seconds")
    async with database.AsyncSessionLocal() as session:
        await session.execute(_upsert_decision_stmt(run_id, node_name, decision, now))
        await session.commit()


async def claim_decision(run_id: int, node_name: str) -> dict[str, Any] | None:
    """认领该节点最新一条未消费决策（审批器挂起检查时消费）。

    单条 UPDATE..RETURNING 原子认领：并发下恰好一个消费者认领到；
    行保留并写 consumed_at，作为审批审计痕迹。"""
    now = datetime.now().isoformat(timespec="seconds")
    async with database.AsyncSessionLocal() as session:
        target = (
            select(ReviewDecision.id)
            .where(
                ReviewDecision.run_id == run_id,
                ReviewDecision.node_name == node_name,
                ReviewDecision.consumed_at.is_(None),
            )
            .order_by(cast(Any, ReviewDecision.id).desc())
            .limit(1)
            .scalar_subquery()
        )
        result = await session.execute(
            update(ReviewDecision)
            .where(ReviewDecision.id == target)
            .values(consumed_at=now)
            .returning(ReviewDecision.approve, ReviewDecision.reason)
        )
        row = result.first()
        await session.commit()
        if row is None:
            return None
        return {"approve": row.approve, "reason": row.reason}
