"""审批决策持久化
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import update
from sqlmodel import select

from app.core import database
from app.models.review import ReviewDecision


async def create_decision(run_id: int, node_name: str, decision: dict[str, Any]) -> None:
    """写入一条审批决策（浏览器答复）；已有未消费决策则覆盖（后答为准）。
    """
    now = datetime.now().isoformat(timespec="seconds")
    async with database.AsyncSessionLocal() as session:
        pending = (
            await session.exec(
                select(ReviewDecision).where(
                    ReviewDecision.run_id == run_id,
                    ReviewDecision.node_name == node_name,
                    ReviewDecision.consumed_at.is_(None),
                )
            )
        ).first()
        if pending is None:
            session.add(
                ReviewDecision(
                    run_id=run_id,
                    node_name=node_name,
                    approve=decision["approve"],
                    reason=decision.get("reason"),
                    edits=decision.get("edits"),
                    payload=decision.get("payload"),
                    created_at=now,
                )
            )
        else:
            pending.approve = decision["approve"]
            pending.reason = decision.get("reason")
            pending.edits = decision.get("edits")
            pending.payload = decision.get("payload")
            pending.created_at = now
        await session.commit()


async def claim_decision(run_id: int, node_name: str) -> dict[str, Any] | None:
    """认领该节点最新一条未消费决策（审批器挂起检查时消费）。"""
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
            .returning(ReviewDecision.approve, ReviewDecision.reason, ReviewDecision.edits)
        )
        row = result.first()
        await session.commit()
        if row is None:
            return None
        return {"approve": row.approve, "reason": row.reason, "edits": row.edits}
