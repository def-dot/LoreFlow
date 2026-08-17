"""声明式配置层 — build_dag/load_dag 解析与校验"""

from typing import Any

import pytest

from app.demo import FUNCTIONS
from app.engine import DAG, RetryPolicy, build_dag, load_dag
from app.engine.declarative import _parse_retry


async def test_build_dag_from_dict_runs() -> None:
    config = {
        "name": "cfg_demo",
        "nodes": {
            "fetch": {"type": "cfg_fetch"},
            "clean": {"type": "cfg_clean", "depends_on": ["fetch"]},
        },
    }
    dag = build_dag(config, functions=FUNCTIONS)
    assert dag.name == "cfg_demo"
    results = await dag.run()
    assert results["clean"].output == "declarative config rocks"


async def test_build_dag_with_human_node() -> None:
    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True}

    config = {
        "nodes": {
            "data": {"type": "cfg_fetch"},
            "review": {"kind": "human", "depends_on": ["data"], "prompt": "check it"},
        },
    }
    dag = build_dag(config, functions=FUNCTIONS, approver=approver)
    results = await dag.run()
    assert results["review"].is_success


async def test_build_dag_loop() -> None:
    def keep_looping(ctx: dict[str, Any], iteration: int) -> bool:
        return iteration < 1

    async def tick(ctx: dict[str, Any]) -> int:
        return ctx.get("tick", 0) + 1

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
    dag = build_dag(config, functions={"tick": tick, "keep_looping": keep_looping})
    results = await dag.run()
    assert results["batch"].is_success


async def test_dotted_path_resolution_without_registry() -> None:
    config = {
        "nodes": {"a": {"type": "app.demo.functions.cfg_fetch"}},
    }
    dag = build_dag(config, functions=None)
    results = await dag.run()
    assert results["a"].is_success


def test_parse_retry_forms() -> None:
    shorthand = _parse_retry(3)
    assert shorthand is not None and shorthand.max_retries == 3

    mapping = _parse_retry({"max_retries": 2, "retry_on": ["RuntimeError"]})
    assert mapping is not None
    assert mapping.max_retries == 2
    assert mapping.retry_on == (RuntimeError,)

    assert _parse_retry(None) is None
    with pytest.raises(ValueError, match="Invalid retry spec"):
        _parse_retry("not-an-int")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown exception"):
        _parse_retry({"retry_on": ["NoSuchError"]})


def test_validation_errors() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        build_dag({"nodes": {"a": {"kind": "quantum"}}}, functions=FUNCTIONS)
    with pytest.raises(ValueError, match="unsupported field"):
        build_dag({"nodes": {"a": {"type": "cfg_fetch", "bogus": 1}}}, functions=FUNCTIONS)
    with pytest.raises(ValueError, match="requires a 'type'"):
        build_dag({"nodes": {"a": {}}}, functions=FUNCTIONS)
    with pytest.raises(ValueError, match="Unknown node 'a' type 'no_such_fn'"):
        build_dag({"nodes": {"a": {"type": "no_such_fn"}}}, functions=FUNCTIONS)
    with pytest.raises(ValueError, match="does not exist"):
        build_dag(
            {"nodes": {"a": {"type": "cfg_fetch", "depends_on": ["ghost"]}}},
            functions=FUNCTIONS,
        )
    with pytest.raises(ValueError, match="Cycle detected"):
        build_dag(
            {
                "nodes": {
                    "a": {"type": "cfg_fetch", "depends_on": ["b"]},
                    "b": {"type": "cfg_fetch", "depends_on": ["a"]},
                }
            },
            functions=FUNCTIONS,
        )
    with pytest.raises(ValueError, match="non-empty 'body'"):
        build_dag({"nodes": {"l": {"kind": "loop", "condition": "x"}}}, functions=FUNCTIONS)
    with pytest.raises(ValueError, match="requires a 'condition'"):
        build_dag({"nodes": {"l": {"kind": "loop", "body": {"t": {"type": "cfg_fetch"}}}}}, functions=FUNCTIONS)
    with pytest.raises(ValueError, match="requires an approver"):
        build_dag({"nodes": {"r": {"kind": "human"}}}, functions=FUNCTIONS)
    with pytest.raises(ValueError, match="must be a dict"):
        build_dag("not-a-dict", functions=FUNCTIONS)  # type: ignore[arg-type]


def test_load_dag_from_yaml(tmp_path) -> None:
    p = tmp_path / "pipeline.yaml"
    p.write_text(
        "name: tiny\nnodes:\n  fetch:\n    type: cfg_fetch\n    retry: 2\n",
        encoding="utf-8",
    )
    dag = load_dag(str(p), functions=FUNCTIONS)
    assert isinstance(dag, DAG)
    assert dag.name == "tiny"
    assert dag.nodes["fetch"].retry == RetryPolicy(max_retries=2)
