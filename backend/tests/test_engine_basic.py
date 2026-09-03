"""引擎基础行为 — 串行/并行/并发上限/超时/resume 快照"""

import asyncio
from typing import Any

import pytest

from app.engine import DAG, DAGExecutionError, Node, NodeStatus, RetryPolicy
from app.engine.validate import validate_config, validate_inputs


async def test_serial_chain() -> None:
    dag = DAG("serial")

    @dag.node("A")
    async def node_a(ctx: dict[str, Any]) -> str:
        return "data_from_A"

    @dag.node("B", depends_on=["A"])
    async def node_b(ctx: dict[str, Any]) -> str:
        return f"processed({ctx['A']})"

    @dag.node("C", depends_on=["B"])
    async def node_c(ctx: dict[str, Any]) -> str:
        return f"finalized({ctx['B']})"

    results = await dag.run()
    assert results["A"].status == NodeStatus.COMPLETED
    assert results["C"].output == "finalized(processed(data_from_A))"


async def test_parallel_fanout() -> None:
    """root 的三个下游应并发执行（用并发计数器断言，免时序 flake）。"""
    active = 0
    max_active = 0

    async def tracked(ctx: dict[str, Any], name: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.05)
        finally:
            active -= 1
        return name

    dag = DAG("parallel")

    @dag.node("root")
    async def root(ctx: dict[str, Any]) -> str:
        return "go"

    for name in ("b", "c", "d"):
        dag.add_node(
            Node(
                name=name,
                func=lambda ctx, n=name: tracked(ctx, n),
                depends_on=["root"],
            )
        )

    @dag.node("join", depends_on=["b", "c", "d"])
    async def join(ctx: dict[str, Any]) -> str:
        return "|".join(ctx[n] for n in ("b", "c", "d"))

    results = await dag.run()
    assert results["join"].status == NodeStatus.COMPLETED
    assert max_active >= 3  # b/c/d 确实并发执行


async def test_concurrency_limit() -> None:
    active = 0
    max_active = 0

    async def tracked(ctx: dict[str, Any], name: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(0.03)
        finally:
            active -= 1
        return name

    dag = DAG("limited")
    for name in ("a", "b", "c"):
        dag.add_node(Node(name=name, func=lambda ctx, n=name: tracked(ctx, n)))

    await dag.run(concurrency=1)
    assert max_active == 1


async def test_timeout_fails_node() -> None:
    dag = DAG("timeout")

    @dag.node("slow", timeout=0.05)
    async def slow(ctx: dict[str, Any]) -> str:
        await asyncio.sleep(10)
        return "never"

    with pytest.raises(DAGExecutionError) as excinfo:
        await dag.run()
    assert excinfo.value.results["slow"].status == NodeStatus.FAILED
    assert isinstance(excinfo.value.results["slow"].error, TimeoutError)


async def test_resume_skips_completed_nodes() -> None:
    """resume 快照中已完成的节点不重跑，其输出直接进上下文。"""
    calls = {"done_node": 0}

    dag = DAG("resume")

    @dag.node("done_node")
    async def done_node(ctx: dict[str, Any]) -> str:
        calls["done_node"] += 1
        return "cached"

    @dag.node("after", depends_on=["done_node"])
    async def after(ctx: dict[str, Any]) -> str:
        return ctx["done_node"]

    results = await dag.run(
        resume={
            "done_node": {"status": "completed", "output": "cached"},
        }
    )
    assert calls["done_node"] == 0
    assert results["after"].output == "cached"
    assert results["done_node"].status == NodeStatus.COMPLETED


async def test_resume_ignores_nodes_missing_from_current_dag() -> None:
    """快照来自旧版配置（节点已被删/改名）时不崩溃：未知节点跳过，其余照常续跑。"""
    dag = DAG("evolved")

    @dag.node("fresh")
    async def fresh(ctx: dict[str, Any]) -> str:
        return "ran"

    results = await dag.run(
        resume={
            "输入内容": {"status": "completed", "output": {"title": "旧配置的节点"}},
            "fresh": {"status": "completed", "output": "stale"},  # 已完成不重跑
        }
    )
    assert results["fresh"].status == NodeStatus.COMPLETED
    assert results["fresh"].output == "stale"


async def test_default_inputs_applied() -> None:
    dag = DAG("inputs", params={"seed": {"default": 41}})

    @dag.node("calc")
    async def calc(ctx: dict[str, Any]) -> int:
        return ctx["seed"] + 1

    results = await dag.run()
    assert results["calc"].output == 42


async def test_retry_policy_delay_bounds() -> None:
    policy = RetryPolicy(max_retries=10, backoff_base=1.0, backoff_max=5.0, jitter=False)
    assert policy.get_delay(0) == 1.0
    assert policy.get_delay(2) == 4.0
    assert policy.get_delay(10) == 5.0  # 封顶 backoff_max


def test_add_node_requires_callable_func() -> None:
    """func 不可调用在注册期拦截（add_node 是所有注册路径的漏斗），不等执行才 TypeError。"""
    dag = DAG("not_callable")

    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    with pytest.raises(ValueError, match="func 必须是可调用对象"):
        dag.add_node(Node(name="bad", func="not_a_function"))
    dag.add_node(Node(name="good", func=ok))  # 拦下坏节点后正常注册不受影响


def test_add_node_requires_callable_condition() -> None:
    dag = DAG("bad_condition")

    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    with pytest.raises(ValueError, match="condition 必须是可调用对象"):
        dag.add_node(Node(name="bad", func=ok, condition=42))


def test_depends_on_wrong_type_fails_validate() -> None:
    """depends_on 类型错（裸字符串/不可迭代）被图校验拦截：
    不再逐字符迭代出「依赖的 'f' 不在 DAG 中」噪音，也不再 TypeError。"""
    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    bare = DAG("dep_str")
    bare.add_node(Node(name="a", func=ok))
    bare.add_node(Node(name="b", func=ok, depends_on="a"))  # 裸字符串
    assert bare.validate() == ["节点 'b': depends_on 必须是字符串列表"]

    uniterable = DAG("dep_int")
    uniterable.add_node(Node(name="a", func=ok, depends_on=5))
    assert uniterable.validate() == ["节点 'a': depends_on 必须是字符串列表"]


def test_depends_on_wrong_type_keeps_node_visible() -> None:
    """类型错的节点仍存在：下游引用它不产生「不在 DAG 中」噪音。"""

    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    dag = DAG("dep_noisy")
    dag.add_node(Node(name="a", func=ok, depends_on=5))
    dag.add_node(Node(name="b", func=ok, depends_on=["a"]))

    assert dag.validate() == ["节点 'a': depends_on 必须是字符串列表"]


def test_no_params_rejects_any_inputs() -> None:
    """params 未声明 → 输入白名单为空：任何输入键都算未声明（此前静默进上下文）。"""
    dag = DAG("no_params")

    @dag.node("a")
    async def a(ctx: dict[str, Any]) -> int:
        return 1

    assert validate_inputs({"x": 1}, dag.params) == ["未声明的参数键: x"]


# ---------------------------------------------------------------------------
# output_expr 求值
# ---------------------------------------------------------------------------


async def test_output_expr_list_picks_first_non_null() -> None:
    """output_expr 为列表时，按声明顺序取第一个非 null 的节点输出。"""
    dag = DAG("out_list", output_expr=["$a", "$b"])

    @dag.node("a")
    async def a(ctx):
        return "from_a"

    @dag.node("b", depends_on=["a"])
    async def b(ctx):
        return None

    results = await dag.run()
    assert "_output" in results
    assert results["_output"].output == "from_a"
    assert results["_output"].status == NodeStatus.COMPLETED


async def test_output_expr_single_ref() -> None:
    """output_expr 为单个 $node 字符串时直接取该节点输出。"""
    dag = DAG("out_single", output_expr="$x")

    @dag.node("x")
    async def x(ctx):
        return "single"

    results = await dag.run()
    assert results["_output"].output == "single"


async def test_output_expr_all_null_yields_skipped() -> None:
    """所有引用节点输出均为 null 时，_output 状态为 SKIPPED。"""
    dag = DAG("out_all_none", output_expr=["$a", "$b"])

    @dag.node("a")
    async def a(ctx):
        return None

    @dag.node("b", depends_on=["a"])
    async def b(ctx):
        return None

    results = await dag.run()
    assert results["_output"].status == NodeStatus.SKIPPED
    assert results["_output"].output is None


async def test_no_output_expr_no_output_key() -> None:
    """未声明 output_expr 时，results 中不含 _output 键。"""
    dag = DAG("no_output")

    @dag.node("x")
    async def x(ctx):
        return "hello"

    results = await dag.run()
    assert "_output" not in results


def test_output_validation_rejects_missing_node() -> None:
    """output 引用不存在的节点时报错。"""
    errors = validate_config({
        "nodes": {"a": {"type": "test_fetch"}},
        "output": "$nonexistent",
    })
    assert any("nonexistent" in e and "不在 DAG 中" in e for e in errors)


def test_output_validation_rejects_missing_node_in_list() -> None:
    """output 列表中引用不存在的节点时报错。"""
    errors = validate_config({
        "nodes": {"a": {"type": "test_fetch"}},
        "output": ["$a", "$missing"],
    })
    assert any("missing" in e and "不在 DAG 中" in e for e in errors)


def test_output_validation_accepts_valid_refs() -> None:
    """output 引用存在的节点时通过校验。"""
    errors = validate_config({
        "nodes": {
            "a": {"type": "test_fetch"},
            "b": {"type": "test_fetch"},
        },
        "output": ["$a", "$b"],
    })
    assert errors == []


def test_output_validation_accepts_single_ref() -> None:
    """output 单引用存在的节点时通过校验。"""
    errors = validate_config({
        "nodes": {"a": {"type": "test_fetch"}},
        "output": "$a",
    })
    assert errors == []


def test_output_validation_rejects_no_dollar_prefix() -> None:
    """output 引用不带 $ 前缀时报错。"""
    errors = validate_config({
        "nodes": {"a": {"type": "test_fetch"}},
        "output": "a",
    })
    assert any("$" in e for e in errors)
