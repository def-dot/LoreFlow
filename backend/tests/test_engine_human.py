"""人工审核 — 通过 / 拒绝级联 / approver 必填"""

from typing import Any

import pytest

from app.engine import DAG, DAGExecutionError, NodeResult, NodeStatus, SuspendExecution
from app.engine.node import ApproverFunc


def fake_approver(decision: dict[str, Any]) -> ApproverFunc:
    """构造一个直接返回给定决策的 approver。"""

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return decision

    return approver


async def test_human_approve_completes_review() -> None:
    dag = DAG("human")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node("review", depends_on=["data"], approver=fake_approver({"approve": True}))

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> int:
        return ctx["review"]["payload"]["data"]["value"]

    results = await dag.run()
    assert results["review"].status == NodeStatus.COMPLETED
    assert results["review"].output["approved"] is True
    assert results["publish"].output == 42


async def test_human_reject_cascades_skip() -> None:
    dag = DAG("human_reject")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node(
        "review",
        depends_on=["data"],
        approver=fake_approver({"approve": False, "reason": "no good"}),
    )

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> str:
        return "should not run"

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    results = excinfo.value.results
    assert results["review"].status == NodeStatus.FAILED
    assert "no good" in str(results["review"].error)
    assert results["publish"].status == NodeStatus.SKIPPED


async def test_human_node_condition_false_skips_review() -> None:
    """condition 为 False 时跳过审核：approver 不被调用，节点 SKIPPED，下游照跑。"""
    called = False

    async def spy_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"approve": True}

    def needs_review(ctx: dict[str, Any]) -> bool:
        return ctx["data"]["value"] < 10

    dag = DAG("conditional_human")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node(
        "review",
        depends_on=["data"],
        condition=needs_review,
        approver=spy_approver,
    )

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> int:
        return 1

    results = await dag.run()
    assert called is False
    assert results["review"].status == NodeStatus.SKIPPED
    assert results["publish"].output == 1


async def test_human_node_requires_approver() -> None:
    dag = DAG("no_approver")
    with pytest.raises(ValueError):
        dag.human_node("review")


async def test_approver_gets_payload_without_self() -> None:
    seen: dict[str, Any] = {}

    async def spy_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen["node"] = node_name
        seen["payload"] = payload
        return {"approve": True}

    dag = DAG("spy")

    @dag.node("upstream")
    async def upstream(ctx: dict[str, Any]) -> str:
        return "up"

    dag.human_node("review", depends_on=["upstream"], approver=spy_approver)

    await dag.run()
    assert seen["node"] == "review"
    assert "review" not in seen["payload"]
    assert seen["payload"]["upstream"] == "up"


async def test_suspend_propagates_without_terminal_event() -> None:
    """approver 抛 SuspendExecution：dag.run 直接传播（非 DAGExecutionError），
    挂起节点不产生终态事件，下游不执行。"""
    collected: list[NodeResult] = []

    async def on_event(result: NodeResult) -> None:
        collected.append(result)

    async def suspend_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise SuspendExecution("waiting")

    called = False
    dag = DAG("suspend", on_event=on_event)

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> int:
        return 1

    dag.human_node("review", depends_on=["data"], approver=suspend_approver)

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> str:
        nonlocal called
        called = True
        return "x"

    with pytest.raises(SuspendExecution):
        await dag.run()

    assert called is False
    review_statuses = {e.status for e in collected if e.node_name == "review"}
    assert not (
        review_statuses
        & {NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED, NodeStatus.CANCELLED}
    )
