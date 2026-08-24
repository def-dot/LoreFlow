"""引擎基础行为 — 串行/并行/并发上限/超时/resume 快照"""

import asyncio
from typing import Any

import pytest

from app.engine import DAG, DAGExecutionError, Node, NodeStatus, RetryPolicy


async def test_serial_chain() -> None:
    dag = DAG("serial")

    @dag.node("A")
    async def node_a(ctx: dict[str, Any]) -> str:
        return "data_from_A"

    @dag.node("B", depends_on=["A"])
    async def node_b(ctx: dict[str, Any]) -> str:
        return f"processed({ctx['A']})"

    @dag.node("C", depends_on=["B"])
    async def node_c(ctx: dict[str, Any]) -> str:
        return f"finalized({ctx['B']})"

    results = await dag.run()
    assert results["A"].status == NodeStatus.COMPLETED
    assert results["C"].output == "finalized(processed(data_from_A))"


async def test_parallel_fanout() -> None:
    """root 的三个下游应并发执行（用并发计数器断言，免时序 flake）。"""
    active = 0
    max_active = 0

    async def tracked(ctx: dict[str, Any], name: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.05)
        finally:
            active -= 1
        return name

    dag = DAG("parallel")

    @dag.node("root")
    async def root(ctx: dict[str, Any]) -> str:
        return "go"

    for name in ("b", "c", "d"):
        dag.add_node(
            Node(
                name=name,
                func=lambda ctx, n=name: tracked(ctx, n),
                depends_on=["root"],
            )
        )

    @dag.node("join", depends_on=["b", "c", "d"])
    async def join(ctx: dict[str, Any]) -> str:
        return "|".join(ctx[n] for n in ("b", "c", "d"))

    results = await dag.run()
    assert results["join"].status == NodeStatus.COMPLETED
    assert max_active >= 3  # b/c/d 确实并发执行


async def test_concurrency_limit() -> None:
    active = 0
    max_active = 0

    async def tracked(ctx: dict[str, Any], name: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.03)
        finally:
            active -= 1
        return name

    dag = DAG("limited")
    for name in ("a", "b", "c"):
        dag.add_node(Node(name=name, func=lambda ctx, n=name: tracked(ctx, n)))

    await dag.run(concurrency=1)
    assert max_active == 1


async def test_timeout_fails_node() -> None:
    dag = DAG("timeout")

    @dag.node("slow", timeout=0.05)
    async def slow(ctx: dict[str, Any]) -> str:
        await asyncio.sleep(10)
        return "never"

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    assert excinfo.value.results["slow"].status == NodeStatus.FAILED
    assert isinstance(excinfo.value.results["slow"].error, TimeoutError)


async def test_resume_skips_completed_nodes() -> None:
    """resume 快照中已完成的节点不重跑，其输出直接进上下文。"""
    calls = {"done_node": 0}

    dag = DAG("resume")

    @dag.node("done_node")
    async def done_node(ctx: dict[str, Any]) -> str:
        calls["done_node"] += 1
        return "cached"

    @dag.node("after", depends_on=["done_node"])
    async def after(ctx: dict[str, Any]) -> str:
        return ctx["done_node"]

    results = await dag.run(
        resume={
            "done_node": {"status": "completed", "output": "cached"},
        }
    )
    assert calls["done_node"] == 0
    assert results["after"].output == "cached"
    assert results["done_node"].status == NodeStatus.COMPLETED


async def test_resume_ignores_nodes_missing_from_current_dag() -> None:
    """快照来自旧版配置（节点已被删/改名）时不崩溃：未知节点跳过，其余照常续跑。"""
    dag = DAG("evolved")

    @dag.node("fresh")
    async def fresh(ctx: dict[str, Any]) -> str:
        return "ran"

    results = await dag.run(
        resume={
            "输入内容": {"status": "completed", "output": {"title": "旧配置的节点"}},
            "fresh": {"status": "completed", "output": "stale"},  # 已完成不重跑
        }
    )
    assert results["fresh"].status == NodeStatus.COMPLETED
    assert results["fresh"].output == "stale"


async def test_default_inputs_applied() -> None:
    dag = DAG("inputs", params={"seed": {"default": 41}})

    @dag.node("calc")
    async def calc(ctx: dict[str, Any]) -> int:
        return ctx["seed"] + 1

    results = await dag.run()
    assert results["calc"].output == 42


async def test_retry_policy_delay_bounds() -> None:
    policy = RetryPolicy(max_retries=10, backoff_base=1.0, backoff_max=5.0, jitter=False)
    assert policy.get_delay(0) == 1.0
    assert policy.get_delay(2) == 4.0
    assert policy.get_delay(10) == 5.0  # 封顶 backoff_max
