"""人工审核 — 挂起 / 通过 / 拒绝级联"""

from typing import Any

import pytest

from app.engine import (
    DAG,
    DAGExecutionError,
    NodeResult,
    NodeStatus,
    RetryPolicy,
    SuspendExecution,
)


async def run_with_decision(
    dag: DAG,
    decision: dict[str, Any],
    inputs: dict[str, Any] | None = None,
) -> tuple[dict[str, NodeResult], dict[str, Any] | None]:
    """模拟审批流程：首次运行挂起，恢复时传入决策。

    Args:
        dag: DAG 实例
        decision: 审批决策，如 {"approve": True} 或 {"approve": False, "reason": "..."}
        inputs: 可选的输入参数

    Returns:
        (results, output) — 与 dag.run() 相同
    """
    # 首次运行：应该挂起
    with pytest.raises(SuspendExecution) as excinfo:
        await dag.run(inputs=inputs)

    # 提取挂起时的 payload
    suspend_output = excinfo.value.results

    # 构造 resume 数据（模拟 orchestrator 存储决策）
    resume = {}
    for node_name in suspend_output:
        resume[node_name] = {
            "status": "reviewing",
            "output": {
                **suspend_output[node_name],
                "decision": decision,
            },
        }

    # 恢复运行
    return await dag.run(inputs=inputs, resume=resume)


async def test_human_approve_completes_review() -> None:
    dag = DAG("human")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node("review", depends_on=["data"])

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> int:
        return ctx["review"]["result"]["data"]["value"]

    results, _ = await run_with_decision(dag, {"approve": True})
    assert results["review"].status == NodeStatus.COMPLETED
    assert results["review"].output["approve"] is True
    assert results["publish"].output == 42


async def test_human_approve_edits_override_payload() -> None:
    """通过时的审核修订写回共享上下文（下游 ctx[key] 拿修订版）。"""
    dag = DAG("human_edit")

    @dag.node("draft")
    async def draft(ctx: dict[str, Any]) -> str:
        return "草稿有一个错别子"

    dag.human_node("review", depends_on=["draft"])

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> str:
        return ctx["draft"]

    results, _ = await run_with_decision(
        dag,
        {"approve": True, "edits": {"draft": "草稿没有错别字"}},
    )
    assert results["publish"].output == "草稿没有错别字"  # 下游经 ctx 拿到修订版
    assert results["draft"].output == "草稿有一个错别子"  # 上游原始输出未被改动


async def test_human_reject_cascades_skip() -> None:
    dag = DAG("human_reject")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node("review", depends_on=["data"])

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> str:
        return "should not run"

    with pytest.raises(DAGExecutionError) as excinfo:
        await run_with_decision(dag, {"approve": False, "reason": "no good"})
    results = excinfo.value.results
    assert results["review"].status == NodeStatus.FAILED
    assert "no good" in str(results["review"].error)
    assert results["review"].output["approve"] is False
    assert results["review"].output["reason"] == "no good"
    assert results["publish"].status == NodeStatus.UPSTREAM_FAILED


async def test_reject_cascade_transmits_transitively() -> None:
    """多级审核第一级拒绝：失败级联要传到隔代下游。"""
    dag = DAG("multi_reject")

    dag.human_node("first_review")
    dag.human_node("second_review", depends_on=["first_review"])

    @dag.node("publish", depends_on=["second_review"])
    async def publish(ctx: dict[str, Any]) -> str:
        return "should not run"

    with pytest.raises(SuspendExecution):
        await dag.run()

    # 模拟初审拒绝
    resume = {
        "first_review": {
            "status": "reviewing",
            "output": {
                "payload": {},
                "labels": {},
                "decision": {"approve": False, "reason": "初审不过"},
            },
        },
    }

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run(resume=resume)
    results = excinfo.value.results
    assert results["first_review"].status == NodeStatus.FAILED
    assert results["second_review"].status == NodeStatus.UPSTREAM_FAILED
    assert results["publish"].status == NodeStatus.UPSTREAM_FAILED


async def test_human_reject_bypasses_retry() -> None:
    """拒绝是终局决策：不进重试循环，attempts 保持 1。"""
    events: list[NodeResult] = []

    async def on_event(result: NodeResult) -> None:
        events.append(result)

    dag = DAG("reject_no_retry", on_event=on_event)
    dag.human_node("review", retry=RetryPolicy(max_retries=2))

    with pytest.raises(DAGExecutionError) as excinfo:
        await run_with_decision(dag, {"approve": False, "reason": "no"})
    results = excinfo.value.results
    assert results["review"].status == NodeStatus.FAILED
    assert results["review"].attempts == 1
    assert not any(e.status == NodeStatus.RETRYING for e in events)


async def test_human_node_condition_false_skips_review() -> None:
    """condition 为 False 时跳过审核：节点 SKIPPED，下游 UPSTREAM_SKIPPED。"""

    def needs_review(ctx: dict[str, Any]) -> bool:
        return ctx["data"]["value"] < 10

    dag = DAG("conditional_human")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node("review", depends_on=["data"], condition=needs_review)

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> int:
        return 1

    results, _ = await dag.run()
    assert results["review"].status == NodeStatus.SKIPPED
    assert results["publish"].status == NodeStatus.UPSTREAM_SKIPPED


async def test_suspend_propagates_without_terminal_event() -> None:
    """human 节点抛 SuspendExecution：dag.run 直接传播，
    挂起节点产生 REVIEWING 事件而非终态事件，下游不执行。"""
    collected: list[NodeResult] = []

    async def on_event(result: NodeResult) -> None:
        collected.append(result)

    called = False
    dag = DAG("suspend", on_event=on_event)

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> int:
        return 1

    dag.human_node("review", depends_on=["data"])

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> str:
        nonlocal called
        called = True
        return "x"

    with pytest.raises(SuspendExecution):
        await dag.run()

    assert called is False
    review_statuses = {e.status for e in collected if e.node_name == "review"}
    assert review_statuses == {NodeStatus.RUNNING, NodeStatus.REVIEWING}
    assert all(e.node_name != "publish" for e in collected)


def test_dag_human_nodes_property() -> None:
    """human_nodes 属性只列出人工审核节点。"""
    dag = DAG("human_nodes")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {}

    dag.human_node("review", depends_on=["data"])

    assert [node.name for node in dag.human_nodes] == ["review"]


def test_review_unknown_key_fails_validate() -> None:
    """review 引用未声明的键（拼错）：校验拦截。"""
    dag = DAG("review_typo")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node(
        "review",
        depends_on=["data"],
        review={"dat": {"label": "数据"}},  # 拼错：data → dat
    )

    assert any("review 引用了未声明的键" in e for e in dag.validate())


def test_review_malformed_format_fails_validate() -> None:
    """程序化 review 富映射缺 label：校验拦截。"""
    dag = DAG("bad_review")
    dag.human_node("review", review={"data": {"text": "数据"}})
    assert any("label 必须是字符串" in e for e in dag.validate())


def test_review_empty_declaration_fails_validate() -> None:
    """空声明（{} / []）与未声明（None）不同：是无意义的视图，校验拦截。"""
    dag = DAG("empty_review")
    dag.human_node("review", review={})
    assert any("review 声明不能为空映射" in e for e in dag.validate())


async def test_review_keys_may_reference_params_and_nodes() -> None:
    """合法声明（键 ∈ 节点名 ∪ params）照常通过，且审核视图只含声明键。"""
    dag = DAG("review_view_ok", params={"topic": {"default": "RAG"}})

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node(
        "review",
        depends_on=["data"],
        review={"topic": {"label": "主题"}, "data": {"label": "数据"}},
    )

    # 首次运行：挂起，验证 payload 包含声明的键
    with pytest.raises(SuspendExecution) as excinfo:
        await dag.run()

    payload = excinfo.value.results.get("review", {}).get("payload", {})
    assert set(payload.keys()) == {"_review", "topic", "data"}