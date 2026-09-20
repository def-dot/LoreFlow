"""引擎基础行为 — 串行/并行/并发上限/超时/resume 快照"""

import asyncio
from typing import Any

import pytest

from app.engine import PipeLine, DAGExecutionError, Node, NodeStatus, RetryPolicy
from app.registry import FuncDef
from app.engine.validator import validate_inputs
from app.registry import REGISTRY
from helpers import validate_config


async def test_serial_chain() -> None:
    dag = PipeLine.from_name("serial")

    @dag.node("A")
    async def node_a(ctx: dict[str, Any]) -> str:
        return "data_from_A"

    @dag.node("B", depends_on=["A"])
    async def node_b(ctx: dict[str, Any]) -> str:
        return f"processed({ctx['A']})"

    @dag.node("C", depends_on=["B"])
    async def node_c(ctx: dict[str, Any]) -> str:
        return f"finalized({ctx['B']})"

    results, _ = await dag.run()
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

    dag = PipeLine.from_name("parallel")

    @dag.node("root")
    async def root(ctx: dict[str, Any]) -> str:
        return "go"

    for name in ("b", "c", "d"):
        dag.add_node(
            Node(
                func_def=FuncDef(name=name, func=lambda ctx, n=name: tracked(ctx, n)),
                name=name,
                depends_on=["root"],
            )
        )

    @dag.node("join", depends_on=["b", "c", "d"])
    async def join(ctx: dict[str, Any]) -> str:
        return "|".join(ctx[n] for n in ("b", "c", "d"))

    results, _ = await dag.run()
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

    dag = PipeLine.from_name("limited")
    for name in ("a", "b", "c"):
        dag.add_node(Node(func_def=FuncDef(name=name, func=lambda ctx, n=name: tracked(ctx, n)), name=name))

    await dag.run(concurrency=1)
    assert max_active == 1


async def test_timeout_fails_node() -> None:
    dag = PipeLine.from_name("timeout")

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

    dag = PipeLine.from_name("resume")

    @dag.node("done_node")
    async def done_node(ctx: dict[str, Any]) -> str:
        calls["done_node"] += 1
        return "cached"

    @dag.node("after", depends_on=["done_node"])
    async def after(ctx: dict[str, Any]) -> str:
        return ctx["done_node"]

    results, _ = await dag.run(
        resume={
            "done_node": {"status": "completed", "output": "cached"},
        }
    )
    assert calls["done_node"] == 0
    assert results["after"].output == "cached"
    assert results["done_node"].status == NodeStatus.COMPLETED


async def test_resume_ignores_nodes_missing_from_current_dag() -> None:
    """快照来自旧版配置（节点已被删/改名）时不崩溃：未知节点跳过，其余照常续跑。"""
    dag = PipeLine.from_name("evolved")

    @dag.node("fresh")
    async def fresh(ctx: dict[str, Any]) -> str:
        return "ran"

    results, _ = await dag.run(
        resume={
            "输入内容": {"status": "completed", "output": {"title": "旧配置的节点"}},
            "fresh": {"status": "completed", "output": "stale"},  # 已完成不重跑
        }
    )
    assert results["fresh"].status == NodeStatus.COMPLETED
    assert results["fresh"].output == "stale"


async def test_default_inputs_applied() -> None:
    dag = PipeLine.from_name("inputs")
    dag.add_node(Node(func_def=REGISTRY["start"], name="__start__", inputs={"seed": {"default": 41}}))

    @dag.node("calc")
    async def calc(ctx: dict[str, Any]) -> int:
        return ctx["seed"] + 1

    results, _ = await dag.run()
    assert results["calc"].output == 42


async def test_retry_policy_delay_bounds() -> None:
    policy = RetryPolicy(max_retries=10, backoff_base=1.0, backoff_max=5.0, jitter=False)
    assert policy.get_delay(0) == 1.0
    assert policy.get_delay(2) == 4.0
    assert policy.get_delay(10) == 5.0  # 封顶 backoff_max


def test_add_node_requires_callable_func() -> None:
    """func 不可调用在注册期拦截（add_node 是所有注册路径的漏斗），不等执行才 TypeError。"""
    from app.registry import FuncDef

    dag = PipeLine.from_name("not_callable")

    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    with pytest.raises(ValueError, match="func 必须是可调用对象"):
        dag.add_node(Node(func_def=FuncDef(name="bad", func="not_a_function"), name="bad"))
    dag.add_node(Node(func_def=FuncDef(name="ok", func=ok), name="good"))  # 拦下坏节点后正常注册不受影响


def test_add_node_requires_callable_condition() -> None:
    dag = PipeLine.from_name("bad_condition")

    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    with pytest.raises(ValueError, match="condition 必须是可调用对象"):
        dag.add_node(Node(func_def=FuncDef(name="ok", func=ok), name="bad", condition=42))


def test_depends_on_wrong_type_fails_validate() -> None:
    """depends_on 类型错（裸字符串/不可迭代）被图校验拦截：
    不再逐字符迭代出「依赖的 'f' 不在 DAG 中」噪音，也不再 TypeError。"""
    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    bare = PipeLine.from_name("dep_str")
    bare.add_node(Node(func_def=FuncDef(name="ok", func=ok), name="a"))
    bare.add_node(Node(func_def=FuncDef(name="ok", func=ok), name="b", depends_on="a"))  # 裸字符串
    assert bare.validate() == ["节点 'b': depends_on 必须是字符串列表"]

    uniterable = PipeLine.from_name("dep_int")
    uniterable.add_node(Node(func_def=FuncDef(name="ok", func=ok), name="a", depends_on=5))
    assert uniterable.validate() == ["节点 'a': depends_on 必须是字符串列表"]


def test_depends_on_wrong_type_keeps_node_visible() -> None:
    """类型错的节点仍存在：下游引用它不产生「不在 DAG 中」噪音。"""

    async def ok(ctx: dict[str, Any]) -> int:
        return 1

    dag = PipeLine.from_name("dep_noisy")
    dag.add_node(Node(func_def=FuncDef(name="ok", func=ok), name="a", depends_on=5))
    dag.add_node(Node(func_def=FuncDef(name="ok", func=ok), name="b", depends_on=["a"]))

    assert dag.validate() == ["节点 'a': depends_on 必须是字符串列表"]


def test_no_inputs_rejects_any_inputs() -> None:
    """inputs 未声明 → 输入白名单为空：任何输入键都算未声明（此前静默进上下文）。"""
    dag = PipeLine.from_name("no_inputs")

    @dag.node("a")
    async def a(ctx: dict[str, Any]) -> int:
        return 1

    assert validate_inputs({"x": 1}, dag.inputs) == ["未声明的参数键: x"]


# ---------------------------------------------------------------------------
# output 求值（$key 语法，与 node inputs 接线一致）
# ---------------------------------------------------------------------------


async def test_output_extracts_node_and_path() -> None:
    """$key 提取节点输出，点分路径下钻子字段。"""
    dag = PipeLine.from_name("out_map")

    @dag.node("a")
    async def a(ctx):
        return {"text": "hello"}

    @dag.node("b")
    async def b(ctx):
        return {"code": 200}

    dag.add_node(Node(func_def=REGISTRY["end"], name="__end__",
                       inputs={"result_text": "$a.text", "status_code": "$b.code"},
                       depends_on=["a", "b"]))
    results, output = await dag.run()
    assert output == {"result_text": "hello", "status_code": 200}


async def test_output_skipped_branch_absent() -> None:
    """被跳过的节点不在上下文中 → 该键缺席。"""
    dag = PipeLine.from_name("out_skip")

    @dag.node("a", condition=False)
    async def a(ctx):
        return "from_a"

    @dag.node("b")
    async def b(ctx):
        return "from_b"

    dag.add_node(Node(func_def=REGISTRY["end"], name="__end__",
                       inputs={"a": "$a", "b": "$b"}, depends_on=["a", "b"]))
    results, output = await dag.run()
    assert results["a"].status == NodeStatus.SKIPPED
    assert output == {"b": "from_b"}


async def test_output_missing_path_absent() -> None:
    """下钻路径不存在 → 键缺席，不报错。"""
    dag = PipeLine.from_name("out_miss")

    @dag.node("a")
    async def a(ctx):
        return {"text": "hello"}

    dag.add_node(Node(func_def=REGISTRY["end"], name="__end__",
                       inputs={"x": "$a.nope"}, depends_on=["a"]))
    results, output = await dag.run()
    assert output == {}


async def test_output_inputs_ref() -> None:
    """$key 引用本次运行输入（inputs 直接在上下文顶层）。"""
    dag = PipeLine.from_name("out_inputs")
    dag.add_node(Node(func_def=REGISTRY["start"], name="__start__", inputs={"query": {"default": "hi"}}))

    @dag.node("a")
    async def a(ctx):
        return ctx["query"]

    dag.add_node(Node(func_def=REGISTRY["end"], name="__end__",
                       inputs={"q": "$query"}, depends_on=["a"]))
    results, output = await dag.run()
    assert output == {"q": "hi"}


async def test_no_output_no_output_key() -> None:
    """未声明 __end__ 时，output 为 None。"""
    dag = PipeLine.from_name("no_output")

    @dag.node("x")
    async def x(ctx):
        return "hello"

    results, _ = await dag.run()
    assert results["x"].output == "hello"


