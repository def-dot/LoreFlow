"""审批决策持久化 — ReviewDecision 的写入与消费。

决策走"浏览器写入、审批器续跑消费"的模式：/api/approve 落一条决策，
续跑（resume）的 approver 在挂起检查中 take_decision 取走最近一条并删除，
取走后状态推进、继续执行。持久化函数调用时才查找 ``database.AsyncSessionLocal``
——conftest 直接换掉该全局即可换库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlmodel import delete, select

from app.core import database
from app.models.review import ReviewDecision


async def create_decision(run_id: int, node_name: str, decision: dict[str, Any]) -> None:
    """写入一条审批决策（浏览器答复）。"""
    row = ReviewDecision(
        run_id=run_id,
        node_name=node_name,
        approve=decision["approve"],
        reason=decision.get("reason"),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    async with database.AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()


async def take_decision(run_id: int, node_name: str) -> dict[str, Any] | None:
    """取走并删除该节点最近一条决策（审批器挂起检查时消费）。

    单条 DELETE..RETURNING 原子消费：并发下恰好一个消费者取到。"""
    async with database.AsyncSessionLocal() as session:
        target = (
            select(ReviewDecision.id)
            .where(ReviewDecision.run_id == run_id, ReviewDecision.node_name == node_name)
            .order_by(cast(Any, ReviewDecision.id).desc())
            .limit(1)
            .scalar_subquery()
        )
        result = await session.execute(
            delete(ReviewDecision)
            .where(ReviewDecision.id == target)
            .returning(ReviewDecision.approve, ReviewDecision.reason)
        )
        row = result.first()
        await session.commit()
        if row is None:
            return None
        return {"approve": row.approve, "reason": row.reason}
