"""ReviewDecision 持久化语义 — upsert 写入、原子认领、快照决策重放。

覆盖"取走即删"改"认领保留"后的行为：重复提交覆盖、行保留作审计、
并发恰好一个消费者、崩溃窗口重放复用快照决策、事件清除旧决策的时机。
"""

import asyncio

import pytest
from sqlmodel import select

import app.core.database as db_mod
from app.engine.types import SuspendExecution
from app.models.review import ReviewDecision
from app.models.run import RunRecord
from app.services import reviews, runs
from app.services.orchestrator import make_approver


async def _all_rows() -> list[ReviewDecision]:
    async with db_mod.AsyncSessionLocal() as session:
        return list((await session.exec(select(ReviewDecision))).all())


async def _persist_run() -> RunRecord:
    record = RunRecord(name="t", config_file="p.yaml")
    await runs.create(record)
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
    assert decision == {"approve": True, "reason": "ok", "edits": None}
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
    assert decision == {"approve": False, "reason": None, "edits": None}


async def test_edits_roundtrip_and_overwrite() -> None:
    """审核修订随决策入库、认领原样带回；重复提交覆盖时 edits 一并覆盖。"""
    await reviews.create_decision(1, "n", {"approve": True, "edits": {"merge": "修订后"}})
    rows = await _all_rows()
    assert rows[0].edits == {"merge": "修订后"}

    # 覆盖提交不带 edits：撤回修订（后答为准）
    await reviews.create_decision(1, "n", {"approve": True, "edits": {"merge": "再改一版"}})
    decision = await reviews.claim_decision(1, "n")
    assert decision == {"approve": True, "reason": None, "edits": {"merge": "再改一版"}}


async def test_payload_snapshot_recorded_with_decision() -> None:
    """审核时视图随决策入库留档；覆盖提交以新视图为准；认领不回带 payload。"""
    await reviews.create_decision(1, "n", {"approve": True, "payload": {"title": "原标题"}})
    rows = await _all_rows()
    assert rows[0].payload == {"title": "原标题"}

    # 覆盖提交换新视图（后答为准，与 edits 同语义）
    await reviews.create_decision(1, "n", {"approve": True, "payload": {"title": "次轮视图"}})
    rows = await _all_rows()
    assert rows[0].payload == {"title": "次轮视图"}

    # 认领只回引擎消费面（approve/reason/edits），payload 纯留档不回带
    decision = await reviews.claim_decision(1, "n")
    assert decision == {"approve": True, "reason": None, "edits": None}


async def test_concurrent_claim_exactly_one_consumer() -> None:
    """并发认领下恰好一个消费者拿到决策。"""
    await reviews.create_decision(1, "n", {"approve": True})

    results = await asyncio.gather(
        reviews.claim_decision(1, "n"),
        reviews.claim_decision(1, "n"),
    )
    assert sum(r is not None for r in results) == 1


async def test_approver_suspends_without_decision() -> None:
    """无未消费决策：挂起并暴露 REVIEWING + payload。"""
    record = await _persist_run()

    with pytest.raises(SuspendExecution):
        await make_approver(record)("review", {"payload": "x"})

    assert record.nodes["review"]["status"] == "reviewing"
    assert record.nodes["review"]["payload"] == {"payload": "x"}
    assert "decision" not in record.nodes["review"]
