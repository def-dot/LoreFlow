"""循环 — 条件终止 / max_iterations 封顶 / body 失败继续"""

import json
from typing import Any

import pytest

from app.engine import DAG, Node, NodeStatus


async def test_loop_until_condition() -> None:
    dag = DAG("loop")

    @dag.node("prepare")
    async def prepare(ctx: dict[str, Any]) -> list:
        return ["a", "b", "c"]

    async def process_one(ctx: dict[str, Any]) -> str:
        idx = ctx.get("advance", {}).get("idx", 0)
        return ctx["prepare"][idx]

    async def advance(ctx: dict[str, Any]) -> dict:
        idx = ctx.get("advance", {}).get("idx", 0)
        return {"idx": idx + 1}

    dag.loop_node(
        "batch",
        body_nodes=[
            Node(name="process_one", func=process_one),
            Node(name="advance", func=advance, depends_on=["process_one"]),
        ],
        depends_on=["prepare"],
        condition=lambda ctx, i: ctx["advance"]["idx"] < len(ctx["prepare"]),
        max_iterations=10,
    )

    results = await dag.run()
    assert results["batch"].status == NodeStatus.COMPLETED
    # 最后一次迭代的 process_one 结果已合并进上下文
    assert results["batch"].output["process_one"] == "c"
    assert results["batch"].output["advance"]["idx"] == 3


async def test_loop_max_iterations_cap() -> None:
    dag = DAG("loop_cap")

    async def tick(ctx: dict[str, Any]) -> int:
        return ctx.get("tick", 0) + 1

    dag.loop_node(
        "loop",
        body_nodes=[Node(name="tick", func=tick)],
        condition=lambda ctx, i: True,  # 永不满足 → 依赖 max_iterations 封顶
        max_iterations=3,
    )

    results = await dag.run()
    assert results["loop"].status == NodeStatus.COMPLETED
    assert results["loop"].output["tick"] == 3


async def test_loop_body_failure_continues() -> None:
    """body 某节点失败时取 DAGExecutionError.results 继续，循环由 condition 决定。"""
    dag = DAG("loop_fail")

    async def count(ctx: dict[str, Any]) -> int:
        return ctx.get("count", 0) + 1

    async def boom(ctx: dict[str, Any]) -> str:
        raise RuntimeError("body failure")

    dag.loop_node(
        "loop",
        body_nodes=[
            Node(name="count", func=count),
            Node(name="boom", func=boom, depends_on=["count"]),
        ],
        condition=lambda ctx, i: ctx.get("count", 0) < 2,
        max_iterations=10,
    )

    results = await dag.run()
    assert results["loop"].status == NodeStatus.COMPLETED
    assert results["loop"].output["count"] == 2


async def test_loop_node_requires_body() -> None:
    dag = DAG("loop_empty")
    with pytest.raises(ValueError):
        dag.loop_node("loop", body_nodes=[], condition=lambda ctx, i: False)


def test_loop_node_requires_callable_condition() -> None:
    dag = DAG("loop_bad_cond")

    async def tick(ctx: dict[str, Any]) -> int:
        return 1

    with pytest.raises(ValueError, match="condition 必须是可调用对象"):
        dag.loop_node("loop", body_nodes=[Node(name="tick", func=tick)], condition="yes")


def test_loop_body_missing_dep_fails_at_registration() -> None:
    """body 缺失依赖在 loop_node 注册期报，不等首轮 sub.run。"""
    dag = DAG("loop_bad_body")

    async def step(ctx: dict[str, Any]) -> int:
        return 1

    with pytest.raises(ValueError, match="依赖的 'missing' 不在 DAG 中"):
        dag.loop_node(
            "loop",
            body_nodes=[Node(name="step", func=step, depends_on=["missing"])],
            condition=lambda ctx, i: False,
        )


def test_loop_body_cycle_fails_at_registration() -> None:
    """body 内成环同样注册期拦截（与声明层递归校验时机对齐）。"""
    dag = DAG("loop_cycle_body")

    async def a(ctx: dict[str, Any]) -> int:
        return 1

    async def b(ctx: dict[str, Any]) -> int:
        return 2

    with pytest.raises(ValueError, match="循环依赖"):
        dag.loop_node(
            "loop",
            body_nodes=[
                Node(name="a", func=a, depends_on=["b"]),
                Node(name="b", func=b, depends_on=["a"]),
            ],
            condition=lambda ctx, i: False,
        )


async def test_loop_output_snapshot_no_self_reference() -> None:
    """loop 输出是累积上下文的快照：不含自身键、可 JSON 序列化。
    直接返回活引用 ctx 会因执行器 ctx[name] = output 形成自引用，
    快照落库（JSON 列）报 Circular reference detected，run 卡死在 running。"""
    dag = DAG("loop_snap")

    async def tick(ctx: dict[str, Any]) -> int:
        return ctx.get("tick", 0) + 1

    dag.loop_node(
        "batch",
        body_nodes=[Node(name="tick", func=tick)],
        condition=lambda ctx, i: ctx.get("tick", 0) < 3,
        max_iterations=5,
    )

    results = await dag.run()
    assert results["batch"].status == NodeStatus.COMPLETED
    assert results["batch"].output["tick"] == 3
    assert "batch" not in results["batch"].output  # 无自引用
    json.dumps(results["batch"].output)  # 序列化不抛异常
