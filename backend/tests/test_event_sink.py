"""节点快照 — executor 构建 retry_history，on_event 全量写入。"""

from typing import Any
from unittest.mock import AsyncMock, patch

from app.engine import DAG, RetryPolicy
from app.engine.node import Node
from app.models.run import RunRecord
from app.services.orchestrator import run_pipeline


async def test_retry_history_accumulates() -> None:
    """重试 2 次后成功：attempts_log 保留全过程，终态 output 是最后一次的。"""
    attempts = {"n": 0}

    async def flaky(ctx: dict[str, Any]) -> str:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise TimeoutError(f"超时{attempts['n']}")
        return "ok"

    dag = DAG("retry_test")
    dag.add_node(Node(name="flaky", func=flaky, retry=RetryPolicy(max_retries=3)))

    record = RunRecord(nodes={})
    with patch("app.services.orchestrator.runs.save_nodes", new=AsyncMock()):
        await run_pipeline(record, dag)

    entry = record.nodes["flaky"]
    assert entry["status"] == "completed"
    assert entry["output"] == "ok"
    assert entry["attempts"] == 3
    log = entry["attempts_log"]
    assert len(log) == 2
    assert log[0]["attempt"] == 1
    assert "超时1" in log[0]["error"]
    assert log[1]["attempt"] == 2
    assert "超时2" in log[1]["error"]
    assert all("at" in a for a in log)


async def test_retry_history_on_final_failure() -> None:
    """重试耗尽后失败：终态带 attempts_log。"""

    async def always_fail(ctx: dict[str, Any]) -> None:
        raise TimeoutError("boom")

    dag = DAG("fail_test")
    dag.add_node(Node(name="bad", func=always_fail, retry=RetryPolicy(max_retries=2)))

    record = RunRecord(nodes={})
    with patch("app.services.orchestrator.runs.save_nodes", new=AsyncMock()):
        try:
            await run_pipeline(record, dag)
        except Exception:
            pass

    entry = record.nodes["bad"]
    assert entry["status"] == "failed"
    assert len(entry["attempts_log"]) == 2


async def test_no_log_without_retries() -> None:
    """一次成功的节点不产生 attempts_log 键。"""

    async def ok(ctx: dict[str, Any]) -> str:
        return "done"

    dag = DAG("ok_test")
    dag.add_node(Node(name="ok", func=ok))

    record = RunRecord(nodes={})
    with patch("app.services.orchestrator.runs.save_nodes", new=AsyncMock()):
        await run_pipeline(record, dag)

    assert "attempts_log" not in record.nodes["ok"]
    assert record.nodes["ok"]["output"] == "done"
