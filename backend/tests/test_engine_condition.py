"""条件分支 — 条件不满足跳过 / 条件异常跳过 / 跳过沿路径级联、汇合 any-success"""

from typing import Any

from app.engine import DAG, NodeStatus


async def test_condition_false_skips_node_and_cascades() -> None:
    """条件不满足：节点 SKIPPED，且单依赖的下游级联 UPSTREAM_SKIPPED（不执行）。"""
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

    results, _ = await dag.run()
    assert results["premium_flow"].status == NodeStatus.SKIPPED
    # 跳过不等于失败，但沿单一路径要级联：下游不带着缺失输入照跑
    assert results["notify"].status == NodeStatus.UPSTREAM_SKIPPED
    assert results["notify"].output is None


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

    results, _ = await dag.run()
    assert results["premium_flow"].status == NodeStatus.COMPLETED


async def test_condition_raising_skips_node() -> None:
    dag = DAG("cond_raise")

    def bad_condition(ctx: dict[str, Any]) -> bool:
        raise RuntimeError("condition boom")

    @dag.node("maybe", condition=bad_condition)
    async def maybe(ctx: dict[str, Any]) -> str:
        return "ran"

    results, _ = await dag.run()
    assert results["maybe"].status == NodeStatus.SKIPPED


async def test_skip_cascades_transitively() -> None:
    """级联是隔代传递的：条件跳过 → 无条件下游 → 再下游，整条路径 UPSTREAM_SKIPPED。"""
    dag = DAG("cascade")

    @dag.node("router")
    async def router(ctx: dict[str, Any]) -> dict:
        return {"tier": "standard"}

    @dag.node(
        "premium",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"]["tier"] == "premium",
    )
    async def premium(ctx: dict[str, Any]) -> str:
        return "premium"

    @dag.node("mid", depends_on=["premium"])
    async def mid(ctx: dict[str, Any]) -> str:
        return "mid"

    @dag.node("tail", depends_on=["mid"])
    async def tail(ctx: dict[str, Any]) -> str:
        return "tail"

    results, _ = await dag.run()
    assert results["premium"].status == NodeStatus.SKIPPED
    assert results["mid"].status == NodeStatus.UPSTREAM_SKIPPED
    assert results["tail"].status == NodeStatus.UPSTREAM_SKIPPED


async def test_join_runs_when_any_upstream_completed() -> None:
    """汇合（any-success）：依赖里只要有一条路径跑成，汇合节点照常执行——
    互斥分支 + 汇合拓扑下任一支路必跳，全跳才级联的严格语义会让汇合
    节点永远不跑。"""
    dag = DAG("join")

    @dag.node("router")
    async def router(ctx: dict[str, Any]) -> dict:
        return {"tier": "standard"}

    @dag.node(
        "premium_path",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"]["tier"] == "premium",
    )
    async def premium_path(ctx: dict[str, Any]) -> str:
        return "premium"

    @dag.node("standard_path", depends_on=["router"])
    async def standard_path(ctx: dict[str, Any]) -> str:
        return "standard"

    @dag.node("join", depends_on=["premium_path", "standard_path"])
    async def join(ctx: dict[str, Any]) -> str:
        return f"joined:{ctx['standard_path']}"

    results, _ = await dag.run()
    assert results["premium_path"].status == NodeStatus.SKIPPED
    assert results["standard_path"].status == NodeStatus.COMPLETED
    assert results["join"].status == NodeStatus.COMPLETED
    assert results["join"].output == "joined:standard"


async def test_join_skips_when_all_upstreams_skipped() -> None:
    """汇合的全部依赖路径都跳过 → 汇合节点跟着级联跳过。"""
    dag = DAG("join_all_skip")

    @dag.node("router")
    async def router(ctx: dict[str, Any]) -> dict:
        return {"tier": "unknown"}

    @dag.node(
        "premium_path",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"]["tier"] == "premium",
    )
    async def premium_path(ctx: dict[str, Any]) -> str:
        return "premium"

    @dag.node(
        "standard_path",
        depends_on=["router"],
        condition=lambda ctx: ctx["router"]["tier"] == "standard",
    )
    async def standard_path(ctx: dict[str, Any]) -> str:
        return "standard"

    @dag.node("join", depends_on=["premium_path", "standard_path"])
    async def join(ctx: dict[str, Any]) -> str:
        return "joined"

    results, _ = await dag.run()
    assert results["premium_path"].status == NodeStatus.SKIPPED
    assert results["standard_path"].status == NodeStatus.SKIPPED
    assert results["join"].status == NodeStatus.UPSTREAM_SKIPPED


async def test_resume_restores_skipped_without_reeval() -> None:
    """快照里条件跳过/级联跳过的节点恢复为既成事实：condition 不重评估、
    节点不重跑；级联语义跨 resume 一致（下游仍是级联跳过）。"""
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

    first, _ = await dag.run()
    assert first["premium_flow"].status == NodeStatus.SKIPPED
    assert first["notify"].status == NodeStatus.UPSTREAM_SKIPPED
    assert calls == 1

    snapshot = {name: r.to_dict() for name, r in first.items()}
    second, _ = await dag.run(resume=snapshot)
    assert second["premium_flow"].status == NodeStatus.SKIPPED  # 仍是跳过，非重评估
    assert second["notify"].status == NodeStatus.UPSTREAM_SKIPPED  # 既成事实不重跑
    assert calls == 1  # 条件未被再次调用
