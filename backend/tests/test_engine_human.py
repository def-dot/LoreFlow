"""人工审核 — 通过 / 拒绝级联 / approver 必填"""

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


async def test_human_approve_edits_override_payload() -> None:
    """通过时的审核修订写回共享上下文（下游 ctx[key] 拿修订版）；
    决策记录里的 payload 是审核时快照，保持原样不被修订覆盖；修订留档
    在 decision.edits；上游 NodeResult 输出保持不动。"""
    dag = DAG("human_edit")

    @dag.node("draft")
    async def draft(ctx: dict[str, Any]) -> str:
        return "草稿有一个错别子"

    dag.human_node(
        "review",
        depends_on=["draft"],
        approver=fake_approver({"approve": True, "edits": {"draft": "草稿没有错别字"}}),
    )

    @dag.node("publish", depends_on=["review"])
    async def publish(ctx: dict[str, Any]) -> str:
        return ctx["draft"]

    results = await dag.run()
    assert results["publish"].output == "草稿没有错别字"  # 下游经 ctx 拿到修订版
    assert results["review"].output["payload"]["draft"] == "草稿有一个错别子"  # 快照保持审核时原样
    assert results["draft"].output == "草稿有一个错别子"  # 上游原始输出未被改动
    assert results["review"].output["decision"]["edits"] == {"draft": "草稿没有错别字"}  # 修订留档


async def test_edits_propagate_to_next_review() -> None:
    """多级审核：初审的修订写回共享上下文，终审视图读到修订后的值。

    回归（08_dual_review）：修订曾只合并进初审节点自己的决策记录，
    终审按 review 键重读 ctx（原始输入），终审卡片显示修订前的内容。
    """
    seen: dict[str, Any] = {}

    async def first_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True, "edits": {"title": "修订后的标题"}}

    async def final_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen["payload"] = payload
        return {"approve": True}

    dag = DAG(
        "dual_review",
        params={"title": {"required": True}, "content": {"required": True}},
    )
    view = {"title": {"label": "标题"}, "content": {"label": "正文"}}
    dag.human_node("编辑初审", approver=first_approver, review=view)
    dag.human_node("主编终审", depends_on=["编辑初审"], approver=final_approver, review=view)

    results = await dag.run(inputs={"title": "原始标题", "content": "正文"})
    assert results["主编终审"].status == NodeStatus.COMPLETED
    assert seen["payload"]["title"] == "修订后的标题"
    assert seen["payload"]["content"] == "正文"
    # 初审决策记录的 payload 是审核时快照，保持原样（修订留档 decision.edits）
    assert results["编辑初审"].output["payload"]["title"] == "原始标题"
    assert results["编辑初审"].output["decision"]["edits"] == {"title": "修订后的标题"}


async def test_human_edits_cannot_inject_new_keys() -> None:
    """修订只作用于审核 payload 已有键：借 edits 注入新键进不了共享上下文
    （防越权改写）；决策 payload 快照保持审核时原样，生效值走 ctx 写回。"""
    seen: dict[str, Any] = {}

    dag = DAG("human_inject")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> str:
        return "内容"

    dag.human_node(
        "review",
        depends_on=["data"],
        approver=fake_approver({"approve": True, "edits": {"data": "改", "injected": "新键"}}),
    )

    @dag.node("after", depends_on=["review"])
    async def after(ctx: dict[str, Any]) -> str:
        seen.update(ctx)
        return "ok"

    results = await dag.run()
    payload = results["review"].output["payload"]
    assert payload["data"] == "内容"  # 快照保持审核时原样
    assert "injected" not in payload
    assert seen["data"] == "改"  # 写回只覆盖已有键
    assert "injected" not in seen  # 注入的新键进不了上下文


async def test_human_reject_ignores_edits() -> None:
    """拒绝不应用修订：payload 保持原样，意见走 reason。"""
    dag = DAG("human_reject_edits")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> str:
        return "内容"

    dag.human_node(
        "review",
        depends_on=["data"],
        approver=fake_approver({"approve": False, "reason": "重写", "edits": {"data": "改"}}),
    )

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    assert excinfo.value.results["review"].output["payload"]["data"] == "内容"


def test_dag_human_nodes_property() -> None:
    """human_nodes 属性只列出人工审核节点，普通节点不在内。"""
    dag = DAG("human_nodes")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {}

    dag.human_node("review", depends_on=["data"], approver=fake_approver({"approve": True}))

    assert [node.name for node in dag.human_nodes] == ["review"]


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
    assert results["review"].output["approved"] is False  # 拒绝详情记入 output
    assert results["review"].output["reason"] == "no good"
    assert results["publish"].status == NodeStatus.UPSTREAM_FAILED


async def test_reject_cascade_transmits_transitively() -> None:
    """多级审核第一级拒绝：失败级联要传到隔代下游，发布节点不得执行。

    回归：级联检查曾只拦直接 FAILED 依赖——第二级被跳过后（SKIPPED），
    依赖它的发布节点检查通过照跑，被拒内容发布了出去。
    """
    published: list[str] = []

    dag = DAG("multi_reject")

    dag.human_node("first_review", approver=fake_approver({"approve": False, "reason": "初审不过"}))

    dag.human_node("second_review", depends_on=["first_review"], approver=fake_approver({"approve": True}))

    @dag.node("publish", depends_on=["second_review"])
    async def publish(ctx: dict[str, Any]) -> str:
        published.append("ran")
        return "should not run"

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    results = excinfo.value.results
    assert results["first_review"].status == NodeStatus.FAILED
    assert results["second_review"].status == NodeStatus.UPSTREAM_FAILED
    assert results["publish"].status == NodeStatus.UPSTREAM_FAILED  # 隔代也拦，级联传递
    assert published == []


async def test_human_reject_bypasses_retry() -> None:
    """拒绝是终局决策：返回 FailedOutput 不进重试循环，attempts 保持 1。"""
    events: list[NodeResult] = []

    async def on_event(result: NodeResult) -> None:
        events.append(result)

    dag = DAG("reject_no_retry", on_event=on_event)
    dag.human_node(
        "review",
        retry=RetryPolicy(max_retries=2),
        approver=fake_approver({"approve": False, "reason": "no"}),
    )

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    results = excinfo.value.results
    assert results["review"].status == NodeStatus.FAILED
    assert results["review"].attempts == 1
    assert not any(e.status == NodeStatus.RETRYING for e in events)


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
    assert results["publish"].output == 1  # 条件跳过是分支语义，下游照跑


async def test_human_node_requires_approver() -> None:
    dag = DAG("no_approver")
    with pytest.raises(ValueError):
        dag.human_node("review")


def test_human_node_requires_callable_approver() -> None:
    dag = DAG("bad_approver")
    with pytest.raises(ValueError, match="approver 必须是可调用对象"):
        dag.human_node("review", approver="not_callable")


def test_review_unknown_key_fails_validate() -> None:
    """review 引用未声明的键（拼错）：校验拦截，而非运行时静默显示「未提供」。"""
    dag = DAG("review_typo")

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    dag.human_node(
        "review",
        depends_on=["data"],
        approver=fake_approver({"approve": True}),
        review={"dat": {"label": "数据"}},  # 拼错：data → dat
    )

    assert any("review 引用了未声明的键" in e for e in dag.validate())


def test_review_malformed_format_fails_validate() -> None:
    """程序化 review 富映射缺 label：校验拦截（共享 validate_review），
    而非运行时构造标签映射才 KeyError。"""
    dag = DAG("bad_review")

    dag.human_node(
        "review",
        approver=fake_approver({"approve": True}),
        review={"data": {"text": "数据"}},  # 富映射格式但无 label
    )

    assert any("label 必须是字符串" in e for e in dag.validate())


def test_review_empty_declaration_fails_validate() -> None:
    """空声明（{} / []）与未声明（None）不同：是无意义的视图，校验拦截。"""
    dag = DAG("empty_review")
    dag.human_node(
        "review",
        approver=fake_approver({"approve": True}),
        review={},
    )

    assert any("review 声明不能为空映射" in e for e in dag.validate())


async def test_review_keys_may_reference_params_and_nodes() -> None:
    """合法声明（键 ∈ 节点名 ∪ params）照常通过，且审核视图只含声明键。"""
    dag = DAG("review_view_ok", params={"topic": {"default": "RAG"}})

    @dag.node("data")
    async def data(ctx: dict[str, Any]) -> dict:
        return {"value": 42}

    seen: dict[str, Any] = {}

    async def spy_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen["payload"] = payload
        return {"approve": True}

    dag.human_node(
        "review",
        depends_on=["data"],
        approver=spy_approver,
        review={"topic": {"label": "主题"}, "data": {"label": "数据"}},
    )

    results = await dag.run()
    assert results["review"].status == NodeStatus.COMPLETED
    assert set(seen["payload"]) == {"_review", "topic", "data"}


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
    挂起节点产生 REVIEWING 事件（审核视图随 output 落快照）而非终态事件，
    级联节点无任何事件，下游不执行。"""
    collected: list[NodeResult] = []

    async def on_event(result: NodeResult) -> None:
        collected.append(result)

    async def suspend_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise SuspendExecution("waiting", {"payload": payload})

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
    assert review_statuses == {NodeStatus.RUNNING, NodeStatus.REVIEWING}  # RUNNING 预告 + REVIEWING 挂起，无终态
    review_entry = next(e for e in collected if e.node_name == "review" and e.status is NodeStatus.REVIEWING)
    assert review_entry.output == {"payload": {"data": 1}}  # 审核视图随挂起结果落 output
    assert all(e.node_name != "publish" for e in collected)  # 级联节点不产生任何事件
