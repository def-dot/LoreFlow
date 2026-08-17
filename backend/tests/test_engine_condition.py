"""条件分支 — 条件不满足跳过 / 条件异常跳过 / 下游照常执行"""

from typing import Any

from app.engine import DAG


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
    assert results["premium_flow"].is_skipped
    # 跳过不等于失败——下游依赖的是完成事件，照常执行
    assert results["notify"].is_success


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
    assert results["premium_flow"].is_success


async def test_condition_raising_skips_node() -> None:
    dag = DAG("cond_raise")

    def bad_condition(ctx: dict[str, Any]) -> bool:
        raise RuntimeError("condition boom")

    @dag.node("maybe", condition=bad_condition)
    async def maybe(ctx: dict[str, Any]) -> str:
        return "ran"

    results = await dag.run()
    assert results["maybe"].is_skipped
