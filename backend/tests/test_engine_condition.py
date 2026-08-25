"""条件分支 — 条件不满足跳过 / 条件异常跳过 / 下游照常执行"""

from typing import Any

from app.engine import DAG, NodeStatus


async def test_condition_false_skips_node() -> None:
    dag = DAG("cond")

    @dag.node("router")
    async def router(ctx: dict[str, Any]) -> dict:
        return {"tier": "standard"}

    @dag.node(
        "premium_flow",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"]["tier"] == "premium",
    )
    async def premium_flow(ctx: dict[str, Any]) -> str:
        return "premium"

    @dag.node("notify", depends_on=["premium_flow"])
    async def notify(ctx: dict[str, Any]) -> str:
        return "notified"

    results = await dag.run()
    assert results["premium_flow"].status == NodeStatus.SKIPPED
    # 跳过不等于失败——下游依赖的是完成事件，照常执行
    assert results["notify"].status == NodeStatus.COMPLETED


async def test_condition_true_runs_node() -> None:
    dag = DAG("cond_true")

    @dag.node("router")
    async def router(ctx: dict[str, Any]) -> dict:
        return {"tier": "premium"}

    @dag.node(
        "premium_flow",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"]["tier"] == "premium",
    )
    async def premium_flow(ctx: dict[str, Any]) -> str:
        return "premium"

    results = await dag.run()
    assert results["premium_flow"].status == NodeStatus.COMPLETED


async def test_condition_raising_skips_node() -> None:
    dag = DAG("cond_raise")

    def bad_condition(ctx: dict[str, Any]) -> bool:
        raise RuntimeError("condition boom")

    @dag.node("maybe", condition=bad_condition)
    async def maybe(ctx: dict[str, Any]) -> str:
        return "ran"

    results = await dag.run()
    assert results["maybe"].status == NodeStatus.SKIPPED


async def test_resume_restores_skipped_without_reeval() -> None:
    """快照里条件跳过的节点恢复为既成事实：condition 不重评估、节点不
    重跑；下游照常恢复（分支跳过语义跨 resume 一致）。"""
    calls = 0

    def is_premium(ctx: dict[str, Any]) -> bool:
        nonlocal calls
        calls += 1
        return ctx["router"]["tier"] == "premium"

    dag = DAG("resume_skip")

    @dag.node("router")
    async def router(ctx: dict[str, Any]) -> dict:
        return {"tier": "standard"}

    @dag.node("premium_flow", depends_on=["router"], condition=is_premium)
    async def premium_flow(ctx: dict[str, Any]) -> str:
        return "premium"

    @dag.node("notify", depends_on=["premium_flow"])
    async def notify(ctx: dict[str, Any]) -> str:
        return "notified"

    first = await dag.run()
    assert first["premium_flow"].status == NodeStatus.SKIPPED
    assert first["notify"].status == NodeStatus.COMPLETED
    assert calls == 1

    snapshot = {name: r.to_dict() for name, r in first.items()}
    second = await dag.run(resume=snapshot)
    assert second["premium_flow"].status == NodeStatus.SKIPPED  # 仍是跳过，非重评估
    assert second["notify"].status == NodeStatus.COMPLETED
    assert calls == 1  # 条件未被再次调用
