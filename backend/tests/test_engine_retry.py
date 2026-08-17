"""重试策略 — 重试至成功 / retry_on 过滤 / 默认不重试"""

from typing import Any

import pytest

from app.engine import DAG, DAGExecutionError, RetryPolicy


async def test_retry_then_success() -> None:
    calls = {"n": 0}

    dag = DAG("retry")

    @dag.node("flaky", retry=RetryPolicy(max_retries=3, backoff_base=0.01, jitter=False))
    async def flaky(ctx: dict[str, Any]) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return "ok"

    results = await dag.run()
    assert calls["n"] == 3
    assert results["flaky"].is_success
    assert results["flaky"].attempts == 3


async def test_retry_on_filters_exceptions() -> None:
    dag = DAG("retry_on")

    @dag.node(
        "boom",
        retry=RetryPolicy(max_retries=2, backoff_base=0.01, jitter=False, retry_on=(RuntimeError,)),
    )
    async def boom(ctx: dict[str, Any]) -> str:
        raise ValueError("not retryable")

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    result = excinfo.value.results["boom"]
    assert result.is_failed
    assert result.attempts == 1  # ValueError 不在 retry_on，不重试
    assert isinstance(result.error, ValueError)


async def test_no_retry_by_default() -> None:
    calls = {"n": 0}

    dag = DAG("no_retry")

    @dag.node("boom")
    async def boom(ctx: dict[str, Any]) -> str:
        calls["n"] += 1
        raise RuntimeError("boom")

    with pytest.raises(DAGExecutionError):
        await dag.run()
    assert calls["n"] == 1


async def test_retries_exhausted_records_attempts() -> None:
    dag = DAG("exhausted")

    @dag.node("always_fails", retry=RetryPolicy(max_retries=2, backoff_base=0.01, jitter=False))
    async def always_fails(ctx: dict[str, Any]) -> str:
        raise RuntimeError("nope")

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    result = excinfo.value.results["always_fails"]
    assert result.attempts == 3  # 1 次原始 + 2 次重试
    assert "nope" in str(result.error)
