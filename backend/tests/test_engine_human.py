"""人工审核 — 通过 / 拒绝级联 / approver 必填"""

from typing import Any

import pytest

from app.engine import DAG, DAGExecutionError, NodeStatus
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
