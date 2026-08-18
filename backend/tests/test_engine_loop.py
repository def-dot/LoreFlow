"""循环 — 条件终止 / max_iterations 封顶 / body 失败继续"""

from typing import Any

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
    import pytest

    dag = DAG("loop_empty")
    with pytest.raises(ValueError):
        dag.loop_node("loop", body_nodes=[], condition=lambda ctx, i: False)
