"""审批决策持久化 — ReviewDecision 的写入与消费。

决策走"浏览器写入、审批器轮询取走"的模式：/api/approve 落一条决策，
运行中的 approver 轮询 take_decision 取走最近一条并删除，取走后
状态推进、继续执行。持久化函数调用时才查找 ``database.AsyncSessionLocal``
——conftest 直接换掉该全局即可换库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlmodel import select

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
    """取走并删除该节点最近一条决策（审批器轮询消费）。"""
    async with database.AsyncSessionLocal() as session:
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
