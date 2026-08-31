"""事件汇（make_event_sink）— attempts_log 重试历史的追加与透传"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.engine import NodeResult, NodeStatus
from app.models.run import RunRecord
from app.services.orchestrator import make_event_sink


async def _noop(_record: RunRecord) -> None:
    """事件汇内部 save_nodes 的替身：只测快照组装，不落库。"""
    return None


def _result(status: NodeStatus, attempts: int = 0, error: Exception | None = None) -> NodeResult:
    return NodeResult(node_name="外部API", status=status, attempts=attempts, error=error)


@contextmanager
def _event_sink(record: RunRecord):
    """构造事件汇并屏蔽落库。"""
    with patch("app.services.orchestrator.runs.save_nodes", new=_noop):
        yield make_event_sink(record)


async def test_retry_history_appended_kept_on_success() -> None:
    """重试 2 次后成功：终态输出/尝试数是最后一次的，attempts_log 保留全过程。"""
    record = RunRecord(nodes={})
    with _event_sink(record) as event:
        await event(_result(NodeStatus.RUNNING))
        await event(_result(NodeStatus.RETRYING, attempts=1, error=TimeoutError("模拟超时1")))
        await event(_result(NodeStatus.RETRYING, attempts=2, error=TimeoutError("模拟超时2")))
        await event(NodeResult(node_name="外部API", status=NodeStatus.COMPLETED, attempts=3, output="ok"))

    entry = record.nodes["外部API"]
    assert entry["status"] == "completed"
    assert entry["output"] == "ok"
    assert entry["attempts"] == 3
    log = entry["attempts_log"]
    assert [a["attempt"] for a in log] == [1, 2]
    assert log[0]["error"] == "模拟超时1"
    assert log[1]["error"] == "模拟超时2"
    assert all(a["at"] for a in log)  # 每条带时间戳


async def test_retry_history_kept_on_final_failure() -> None:
    """重试耗尽后失败：终态错误是最后一次的，中间尝试也不丢。"""
    record = RunRecord(nodes={})
    with _event_sink(record) as event:
        await event(_result(NodeStatus.RETRYING, attempts=1, error=TimeoutError("超时1")))
        await event(_result(NodeStatus.RETRYING, attempts=2, error=TimeoutError("超时2")))
        await event(_result(NodeStatus.FAILED, attempts=3, error=TimeoutError("超时3")))

    entry = record.nodes["外部API"]
    assert entry["status"] == "failed"
    assert entry["error"] == "超时3"
    assert [a["attempt"] for a in entry["attempts_log"]] == [1, 2]


async def test_no_log_without_retries() -> None:
    """一次成功的节点不产生 attempts_log 键。"""
    record = RunRecord(nodes={})
    with _event_sink(record) as event:
        await event(_result(NodeStatus.RUNNING))
        await event(NodeResult(node_name="外部API", status=NodeStatus.COMPLETED, attempts=1, output="ok"))

    assert "attempts_log" not in record.nodes["外部API"]


async def test_log_survives_resume_running_event() -> None:
    """resume 重放：RUNNING 事件不冲掉上次进程留下的重试历史。"""
    record = RunRecord(
        nodes={"外部API": {"status": "reviewing", "attempts_log": [{"attempt": 1, "error": "旧错误", "at": "旧时间"}]}}
    )
    with _event_sink(record) as event:
        await event(_result(NodeStatus.RUNNING))

    entry = record.nodes["外部API"]
    assert entry["attempts_log"] == [{"attempt": 1, "error": "旧错误", "at": "旧时间"}]
    assert entry["status"] == "running"
