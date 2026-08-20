"""ReviewDecision 持久化语义 — upsert 写入、原子认领、快照决策重放。

覆盖"取走即删"改"认领保留"后的行为：重复提交覆盖、行保留作审计、
并发恰好一个消费者、崩溃窗口重放复用快照决策、事件清除旧决策的时机。
"""

import asyncio

import pytest
from sqlmodel import select

import app.core.database as db_mod
from app.engine.types import NodeResult, NodeStatus, SuspendExecution
from app.models.review import ReviewDecision
from app.models.run import RunRecord
from app.services import reviews, runs
from app.services.orchestrator import make_approver, make_event_sink


async def _all_rows() -> list[ReviewDecision]:
    async with db_mod.AsyncSessionLocal() as session:
        return list((await session.exec(select(ReviewDecision))).all())


async def _persist_run() -> RunRecord:
    record = RunRecord(name="t", config_file="p.yaml")
    await runs.save(record)
    assert record.id is not None
    return record


async def test_create_overwrites_pending_decision() -> None:
    """重复提交覆盖未消费决策：双击/并发不会残留陈旧行。"""
    await reviews.create_decision(1, "n", {"approve": True, "reason": "first"})
    await reviews.create_decision(1, "n", {"approve": False, "reason": "second"})

    rows = await _all_rows()
    assert len(rows) == 1
    assert rows[0].approve is False
    assert rows[0].reason == "second"
    assert rows[0].consumed_at is None


async def test_claim_returns_decision_and_keeps_row() -> None:
    """认领返回决策、行保留并标记 consumed_at，二次认领为空。"""
    await reviews.create_decision(1, "n", {"approve": True, "reason": "ok"})

    decision = await reviews.claim_decision(1, "n")
    assert decision == {"approve": True, "reason": "ok"}
    assert await reviews.claim_decision(1, "n") is None

    rows = await _all_rows()
    assert len(rows) == 1
    assert rows[0].consumed_at is not None  # 审计痕迹保留


async def test_new_decision_after_consumed_inserts_new_row() -> None:
    """已消费的历史行不参与冲突判定：新提交插入新行，审计历史保留。"""
    await reviews.create_decision(1, "n", {"approve": True})
    await reviews.claim_decision(1, "n")
    await reviews.create_decision(1, "n", {"approve": False, "reason": "changed"})

    rows = await _all_rows()
    assert len(rows) == 2
    pending = [r for r in rows if r.consumed_at is None]
    assert len(pending) == 1
    assert pending[0].approve is False


async def test_claim_only_targets_pending_rows() -> None:
    """认领只看未消费行：已消费的旧决策不会被下一次到达误消费。"""
    await reviews.create_decision(1, "n", {"approve": True})
    await reviews.claim_decision(1, "n")
    await reviews.create_decision(1, "n", {"approve": False})

    decision = await reviews.claim_decision(1, "n")
    assert decision == {"approve": False, "reason": None}


async def test_concurrent_claim_exactly_one_consumer() -> None:
    """并发认领下恰好一个消费者拿到决策。"""
    await reviews.create_decision(1, "n", {"approve": True})

    results = await asyncio.gather(
        reviews.claim_decision(1, "n"),
        reviews.claim_decision(1, "n"),
    )
    assert sum(r is not None for r in results) == 1


async def test_approver_snapshot_decision_replayed_after_crash() -> None:
    """认领后的决策写进节点快照：崩溃重放（新 approver 实例）原样复用，
    不会把已批准过的节点重新挂起。"""
    record = await _persist_run()
    assert record.id is not None
    await reviews.create_decision(record.id, "review", {"approve": True, "reason": "ok"})

    decision = await make_approver(record)("review", {"payload": "x"})
    assert decision == {"approve": True, "reason": "ok"}
    assert record.nodes["review"]["decision"] == decision

    # 模拟崩溃后重启重放：快照里有决策，无需新决策即可续跑
    replayed = await make_approver(record)("review", {})
    assert replayed == decision
    assert record.nodes["review"]["status"] == "running"


async def test_approver_replayed_reject_decision_reusable() -> None:
    """拒绝决策同样写进快照且可被重放复用（dict 非空为真）。"""
    record = await _persist_run()
    assert record.id is not None
    await reviews.create_decision(record.id, "review", {"approve": False, "reason": "no"})

    decision = await make_approver(record)("review", {"payload": "x"})
    assert decision["approve"] is False
    replayed = await make_approver(record)("review", {})
    assert replayed == decision


async def test_approver_suspends_without_decision() -> None:
    """无未消费决策：挂起并暴露 REVIEWING + payload。"""
    record = await _persist_run()

    with pytest.raises(SuspendExecution):
        await make_approver(record)("review", {"payload": "x"})

    assert record.nodes["review"]["status"] == "reviewing"
    assert record.nodes["review"]["payload"] == {"payload": "x"}
    assert "decision" not in record.nodes["review"]


async def test_sink_preserves_decision_on_running_only() -> None:
    """RUNNING 事件保留快照决策（崩溃重放复用）；RETRYING/终态清除，
    防止拒绝重试与 loop 新迭代误复用上一次的决策。"""
    record = await _persist_run()
    record.nodes["review"] = {"status": "reviewing", "decision": {"approve": True}}
    sink = make_event_sink(record)

    await sink(NodeResult(node_name="review", status=NodeStatus.RUNNING))
    assert record.nodes["review"]["decision"] == {"approve": True}

    await sink(NodeResult(node_name="review", status=NodeStatus.RETRYING))
    assert "decision" not in record.nodes["review"]

    record.nodes["review"]["decision"] = {"approve": True}
    await sink(NodeResult(node_name="review", status=NodeStatus.COMPLETED))
    assert "decision" not in record.nodes["review"]
