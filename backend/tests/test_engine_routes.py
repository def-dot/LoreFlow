"""边级路由 — routes 声明、支路门卫合成、未选中支路整条不调度与校验"""

from copy import deepcopy
from typing import Any, Literal

import pytest

from app.engine import NodeStatus, load_dag
from app.engine.declarative import validate_config
from app.engine.node import RouteError
from app.engine.types import DAGExecutionError
from app.registry import REGISTRY, NodeType


@pytest.fixture
def registered() -> Any:
    """临时注册测试用的节点/路由函数，测试结束后自动撤销（直接读写 REGISTRY）。"""
    added: list[str] = []

    def _reg(name: str, func: Any, kind: Literal["function", "condition", "router"]) -> None:
        REGISTRY[name] = NodeType(name=name, func=func, kind=kind, label=name, description=name)
        added.append(name)

    yield _reg
    for name in added:
        REGISTRY.pop(name, None)


@pytest.fixture
def routing(registered: Any) -> dict[str, Any]:
    """确定性路由测试件：route_src 把输入 route 原样写进输出，t_router 读它
    返回支路标签，支路节点 a1→a2 / b1 执行后输出 "ran"。"""
    async def t_route_src(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"route": ctx.get("route")}

    def t_router(ctx: dict[str, Any]) -> Any:
        out = ctx.get("route_src")
        return out.get("route") if isinstance(out, dict) else None

    def t_router_raise(ctx: dict[str, Any]) -> Any:
        raise RuntimeError("router boom")

    async def t_branch(ctx: dict[str, Any]) -> str:
        return "ran"

    registered("t_route_src", t_route_src, "function")
    registered("t_router", t_router, "router")
    registered("t_router_raise", t_router_raise, "router")
    registered("t_branch", t_branch, "function")

    return {
        "params": {"route": {}},
        "nodes": {
            "route_src": {
                "type": "t_route_src",
                "routes": {
                    "router": "t_router",
                    "branches": {"a": ["a1", "a2"], "b": ["b1"]},
                },
            },
            "a1": {"type": "t_branch", "depends_on": ["route_src"]},
            "a2": {"type": "t_branch", "depends_on": ["a1"]},
            "b1": {"type": "t_branch", "depends_on": ["route_src"]},
        },
    }


# ---------------------------------------------------------------------------
# 执行语义 — 选中支路执行、未选中支路整条 SKIPPED、default 与失败
# ---------------------------------------------------------------------------


async def test_unselected_branch_fully_skipped(routing: dict[str, Any]) -> None:
    """命中支路 a：a1/a2 完成，b 整条 SKIPPED（不出输出）；换 b 同理反向。"""
    dag = load_dag(deepcopy(routing))
    # 门卫已合成：源挂 routes，支路成员自带 condition（同一确定性路由函数）
    assert dag.nodes["route_src"].routes is not None
    assert dag.nodes["a1"].condition is not None
    assert dag.nodes["b1"].condition is not None

    results = await dag.run(inputs={"route": "a"})
    assert results["route_src"].status is NodeStatus.COMPLETED
    assert [results[n].status for n in ("a1", "a2")] == [NodeStatus.COMPLETED] * 2
    assert results["b1"].status is NodeStatus.SKIPPED
    assert results["b1"].output is None

    results = await load_dag(deepcopy(routing)).run(inputs={"route": "b"})
    assert [results[n].status for n in ("a1", "a2")] == [NodeStatus.SKIPPED] * 2
    assert results["b1"].status is NodeStatus.COMPLETED


async def test_default_on_unknown_label(routing: dict[str, Any]) -> None:
    """标签未命中 + 声明 default → 回落支路照常执行。"""
    routing["nodes"]["route_src"]["routes"]["default"] = "a"
    results = await load_dag(routing).run(inputs={"route": "zzz"})
    assert [results[n].status for n in ("a1", "a2")] == [NodeStatus.COMPLETED] * 2
    assert results["b1"].status is NodeStatus.SKIPPED


async def test_no_match_without_default_fails(routing: dict[str, Any]) -> None:
    """标签未命中且无 default：路由歧义是错误 —— 源节点 FAILED、成员级联
    UPSTREAM_FAILED，而非静默全部跳过。"""
    with pytest.raises(DAGExecutionError) as exc_info:
        await load_dag(deepcopy(routing)).run(inputs={"route": "zzz"})
    results = exc_info.value.results
    assert results["route_src"].status is NodeStatus.FAILED
    assert isinstance(results["route_src"].error, RouteError)
    assert results["a1"].status is NodeStatus.UPSTREAM_FAILED
    assert results["b1"].status is NodeStatus.UPSTREAM_FAILED


async def test_router_raise_fails_source(routing: dict[str, Any]) -> None:
    """router 抛异常 → 源节点失败（与「返回未知标签」同一显式化路径）。"""
    routing["nodes"]["route_src"]["routes"]["router"] = "t_router_raise"
    with pytest.raises(DAGExecutionError) as exc_info:
        await load_dag(routing).run(inputs={"route": "a"})
    results = exc_info.value.results
    assert results["route_src"].status is NodeStatus.FAILED
    assert isinstance(results["route_src"].error, RuntimeError)


async def test_human_member_skipped_when_unrouted(routing: dict[str, Any]) -> None:
    """human 支路成员未命中 → 门卫先于挂起判 SKIPPED，approver 不被调用。"""
    calls: list[str] = []

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(node_name)
        return {"approve": True}

    routing["nodes"]["takeover"] = {
        "kind": "human",
        "depends_on": ["route_src"],
    }
    routing["nodes"]["route_src"]["routes"]["branches"]["b"] = ["takeover"]

    results = await load_dag(routing, approver=approver).run(inputs={"route": "a"})
    assert results["takeover"].status is NodeStatus.SKIPPED
    assert calls == []


def test_mermaid_labels_branch_entry_edges(routing: dict[str, Any]) -> None:
    """支路入口边带标签，支路内部边不带。"""
    mermaid = load_dag(deepcopy(routing)).to_mermaid()
    assert "route_src -->|a| a1" in mermaid
    assert "route_src -->|b| b1" in mermaid
    assert "a1 --> a2" in mermaid


# ---------------------------------------------------------------------------
# 校验 — validate_routes（经 validate_config / validate_nodes 汇总）
# ---------------------------------------------------------------------------


def test_routes_router_must_be_router_kind(routing: dict[str, Any]) -> None:
    """router 引用普通函数节点 → 报类型不符（bool 谓词不是标签路由）。"""
    routing["nodes"]["route_src"]["routes"]["router"] = "cfg_fetch"
    assert validate_config(routing) == [
        "节点 'route_src': 'cfg_fetch' 是 function 类型，routes.router 必须是 router 类型函数"
    ]


def test_routes_router_unregistered(routing: dict[str, Any]) -> None:
    routing["nodes"]["route_src"]["routes"]["router"] = "no_such_router"
    assert validate_config(routing) == ["节点 'route_src': 路由函数 'no_such_router' 未注册"]


def test_routes_branches_required(routing: dict[str, Any]) -> None:
    del routing["nodes"]["route_src"]["routes"]["branches"]
    assert validate_config(routing) == [
        "节点 'route_src': routes 需要非空的 'branches' 映射（{标签: [成员节点]}）"
    ]


def test_routes_member_must_be_declared(routing: dict[str, Any]) -> None:
    routing["nodes"]["route_src"]["routes"]["branches"]["a"] = ["ghost"]
    assert validate_config(routing) == ["节点 'route_src': 支路 'a' 的成员 'ghost' 不在节点声明中"]


def test_routes_member_conflicts_with_condition(routing: dict[str, Any]) -> None:
    routing["nodes"]["b1"]["condition"] = "cfg_needs_report"
    assert validate_config(routing) == [
        "节点 'route_src': 支路 'b' 的成员 'b1' 已声明 condition（路由门卫与显式条件互斥）"
    ]


def test_routes_member_only_in_one_branch(routing: dict[str, Any]) -> None:
    routing["nodes"]["route_src"]["routes"]["branches"]["b"] = ["b1", "a2"]
    assert validate_config(routing) == [
        "节点 'route_src': 'a2' 重复出现在支路中（已属于支路 'a'；一个节点只能属于一条支路）"
    ]


def test_routes_default_must_be_label(routing: dict[str, Any]) -> None:
    routing["nodes"]["route_src"]["routes"]["default"] = "nope"
    assert validate_config(routing) == ["节点 'route_src': default 'nope' 不是已声明的支路标签"]


def test_routes_member_must_reach_source(routing: dict[str, Any]) -> None:
    """成员不依赖路由源 → 拒绝：门卫可能在源输出就绪前求值。"""
    routing["nodes"]["b1"]["depends_on"] = []
    assert validate_config(routing) == [
        "节点 'route_src': 支路 'b' 的成员 'b1' 未（传递）依赖路由源 —— 门卫须在源输出就绪后求值"
    ]


def test_routes_cross_branch_dependency_rejected(routing: dict[str, Any]) -> None:
    routing["nodes"]["b1"]["depends_on"] = ["route_src", "a1"]
    assert validate_config(routing) == [
        "节点 'route_src': 支路 'b' 的成员 'b1' 依赖了兄弟支路 'a' 的 'a1'（跨支路依赖）"
    ]


def test_routes_loop_member_rejected(routing: dict[str, Any]) -> None:
    routing["nodes"]["b1"] = {
        "kind": "loop",
        "condition": "demo_keep_iterating",
        "body": {"t": {"type": "demo_tick"}},
    }
    routing["nodes"]["b1"]["depends_on"] = ["route_src"]
    assert validate_config(routing) == [
        "节点 'route_src': 支路 'b' 的成员 'b1' 是 loop 节点（其 condition 是循环谓词，不能作支路门卫）"
    ]


def test_routes_on_human_rejected_as_unknown_field() -> None:
    """routes 只能声明在普通 node 上 —— human/loop 报不支持的字段。"""
    with pytest.raises(ValueError, match="不支持的字段"):
        load_dag({"nodes": {
            "gate": {"kind": "human", "routes": {"router": "x", "branches": {"a": ["y"]}}},
        }})
