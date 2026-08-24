"""错误处理 — 失败记录 error / 级联跳过 / DAG 校验"""

from typing import Any

import pytest

from app.engine import DAG, DAGExecutionError, Node, NodeStatus


async def noop(ctx: dict[str, Any]) -> str:
    return "ok"


async def test_failure_records_error_and_skips_downstream() -> None:
    dag = DAG("error")

    @dag.node("critical")
    async def critical(ctx: dict[str, Any]) -> str:
        raise ConnectionError("Service unreachable")

    @dag.node("dependant", depends_on=["critical"])
    async def dependant(ctx: dict[str, Any]) -> str:
        return "should not run"

    @dag.node("independent")
    async def independent(ctx: dict[str, Any]) -> str:
        return "I run regardless"

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    results = excinfo.value.results
    assert results["critical"].status == NodeStatus.FAILED
    assert isinstance(results["critical"].error, ConnectionError)
    assert results["dependant"].status == NodeStatus.UPSTREAM_FAILED
    assert results["independent"].status == NodeStatus.COMPLETED


def test_missing_dependency_rejected() -> None:
    dag = DAG("missing_dep")
    dag.add_node(Node(name="a", func=noop, depends_on=["ghost"]))
    assert any("ghost" in e for e in dag.validate())


def test_cycle_rejected() -> None:
    dag = DAG("cycle")
    dag.add_node(Node(name="a", func=noop, depends_on=["b"]))
    dag.add_node(Node(name="b", func=noop, depends_on=["a"]))
    assert any("循环依赖" in e for e in dag.validate())


def test_empty_dag_rejected() -> None:
    dag = DAG("empty")
    assert dag.validate() == ["DAG 没有节点"]


async def test_duplicate_node_name_rejected() -> None:
    dag = DAG("dup")
    dag.add_node(Node(name="a", func=noop))
    with pytest.raises(ValueError, match="节点名重复"):
        dag.add_node(Node(name="a", func=noop))


async def test_validate_returns_error_list() -> None:
    dag = DAG("invalid")
    dag.add_node(Node(name="a", func=noop, depends_on=["ghost"]))
    errors = dag.validate()
    assert len(errors) == 1
    assert "ghost" in errors[0]
