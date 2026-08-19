"""声明式配置层 — load_dag 解析与校验"""

from typing import Any, Literal

import pytest

from app.engine import DAG, NodeStatus, RetryPolicy, load_dag
from app.engine.resolve import parse_retry
from app.registry import NodeType, register, unregister


@pytest.fixture
def registered() -> Any:
    """临时注册测试用的节点函数，测试结束后自动撤销。"""
    added: list[str] = []

    def _reg(name: str, func: Any, kind: Literal["function", "condition"]) -> None:
        register(NodeType(name=name, func=func, kind=kind, label=name, description=name))
        added.append(name)

    yield _reg
    for name in added:
        unregister(name)


async def test_load_dag_from_dict_runs() -> None:
    config = {
        "name": "cfg_demo",
        "nodes": {
            "fetch": {"type": "cfg_fetch"},
            "clean": {"type": "cfg_clean", "depends_on": ["fetch"]},
        },
    }
    dag = load_dag(config)
    assert dag.name == "cfg_demo"
    results = await dag.run()
    assert results["clean"].output == "declarative config rocks"


async def test_load_dag_with_human_node() -> None:
    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True}

    config = {
        "nodes": {
            "data": {"type": "cfg_fetch"},
            "review": {"kind": "human", "depends_on": ["data"], "prompt": "check it"},
        },
    }
    dag = load_dag(config, approver=approver)
    results = await dag.run()
    assert results["review"].status == NodeStatus.COMPLETED


async def test_load_dag_human_with_condition(registered: Any) -> None:
    """human 节点支持 condition —— False 时跳过审核，approver 不被调用。"""
    calls: list[tuple[str, dict[str, Any]]] = []

    def needs_review(ctx: dict[str, Any]) -> bool:
        return False

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((node_name, payload))
        return {"approve": True}

    registered("needs_review", needs_review, "condition")

    config = {
        "nodes": {
            "data": {"type": "cfg_fetch"},
            "review": {"kind": "human", "depends_on": ["data"], "condition": "needs_review"},
        },
    }
    dag = load_dag(config, approver=approver)
    results = await dag.run()
    assert results["review"].status == NodeStatus.SKIPPED
    assert calls == []


async def test_load_dag_loop(registered: Any) -> None:
    def keep_looping(ctx: dict[str, Any], iteration: int) -> bool:
        return iteration < 1

    async def tick(ctx: dict[str, Any]) -> int:
        return ctx.get("tick", 0) + 1

    registered("tick", tick, "function")
    registered("keep_looping", keep_looping, "condition")

    config = {
        "nodes": {
            "batch": {
                "kind": "loop",
                "body": {"tick": {"type": "tick"}},
                "condition": "keep_looping",
                "max_iterations": 2,
            },
        },
    }
    dag = load_dag(config)
    results = await dag.run()
    assert results["batch"].status == NodeStatus.COMPLETED


def test_registry_only_lookup() -> None:
    # type/condition 只能引用注册表中的名字，没有 functions 参数可传
    dag = load_dag({"nodes": {"a": {"type": "cfg_fetch"}}})
    assert "a" in dag.nodes

    # 点路径不是注册名字，同样被拒绝
    with pytest.raises(ValueError, match="未注册"):
        load_dag({"nodes": {"b": {"type": "app.registry.functions.cfg_fetch"}}})


def test_parse_retry_forms() -> None:
    shorthand = parse_retry(3)
    assert shorthand is not None and shorthand.max_retries == 3

    mapping = parse_retry({"max_retries": 2, "retry_on": ["RuntimeError"]})
    assert mapping is not None
    assert mapping.max_retries == 2
    assert mapping.retry_on == (RuntimeError,)

    assert parse_retry(None) is None
    assert parse_retry(False) is None  # YAML ``retry: no`` = explicit disable
    with pytest.raises(ValueError, match="无效的 retry 配置"):
        parse_retry("not-an-int")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="无效的 retry 配置"):
        parse_retry(True)  # YAML ``retry: yes`` — ambiguous, reject
    with pytest.raises(ValueError, match="未知异常"):
        parse_retry({"retry_on": ["NoSuchError"]})
    with pytest.raises(ValueError, match="无效的 retry_on"):
        parse_retry({"retry_on": [123]})  # type: ignore[list-item]


def test_validation_errors() -> None:
    with pytest.raises(ValueError, match="未知类型"):
        load_dag({"nodes": {"a": {"kind": "quantum"}}})
    with pytest.raises(ValueError, match="不支持的字段"):
        load_dag({"nodes": {"a": {"type": "cfg_fetch", "bogus": 1}}})
    with pytest.raises(ValueError, match="需要 'type'"):
        load_dag({"nodes": {"a": {}}})
    with pytest.raises(ValueError, match="节点 'no_such_fn' 未注册"):
        load_dag({"nodes": {"a": {"type": "no_such_fn"}}})
    with pytest.raises(ValueError, match="不在 DAG 中"):
        load_dag({"nodes": {"a": {"type": "cfg_fetch", "depends_on": ["ghost"]}}})
    with pytest.raises(ValueError, match="循环依赖"):
        load_dag(
            {
                "nodes": {
                    "a": {"type": "cfg_fetch", "depends_on": ["b"]},
                    "b": {"type": "cfg_fetch", "depends_on": ["a"]},
                }
            }
        )
    with pytest.raises(ValueError, match="非空的 'body'"):
        load_dag({"nodes": {"l": {"kind": "loop", "condition": "x"}}})
    with pytest.raises(ValueError, match="需要 'condition'"):
        load_dag({"nodes": {"l": {"kind": "loop", "body": {"t": {"type": "cfg_fetch"}}}}})
    with pytest.raises(ValueError, match="必须提供 approver"):
        load_dag({"nodes": {"r": {"kind": "human"}}})
    with pytest.raises(ValueError, match="必须是 dict"):
        load_dag(123)  # type: ignore[arg-type]


def test_load_dag_from_yaml(tmp_path) -> None:
    p = tmp_path / "pipeline.yaml"
    p.write_text(
        "name: tiny\nnodes:\n  fetch:\n    type: cfg_fetch\n    retry: 2\n",
        encoding="utf-8",
    )
    dag = load_dag(str(p))
    assert isinstance(dag, DAG)
    assert dag.name == "tiny"
    assert dag.nodes["fetch"].retry == RetryPolicy(max_retries=2)


def test_load_dag_bad_file(tmp_path) -> None:
    with pytest.raises(ValueError, match="无法读取配置文件"):
        load_dag(tmp_path / "missing.yaml")

    bad = tmp_path / "bad.yaml"
    bad.write_text("nodes: [unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML 无效"):
        load_dag(bad)
