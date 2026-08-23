"""声明式配置层 — load_dag 解析与校验"""

from typing import Any, Literal

import pytest

from app.engine import DAG, NodeStatus, RetryPolicy, load_dag
from app.engine.declarative import parse_params, parse_review
from app.engine.resolve import parse_retry
from app.registry import REGISTRY, NodeType


@pytest.fixture
def registered() -> Any:
    """临时注册测试用的节点函数，测试结束后自动撤销（直接读写 REGISTRY）。"""
    added: list[str] = []

    def _reg(name: str, func: Any, kind: Literal["function", "condition"]) -> None:
        REGISTRY[name] = NodeType(name=name, func=func, kind=kind, label=name, description=name)
        added.append(name)

    yield _reg
    for name in added:
        REGISTRY.pop(name, None)


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
        load_dag({"nodes": {"b": {"type": "app.registry.nodes.cfg_fetch"}}})


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


# ---------------------------------------------------------------------------
# required_inputs — 必填输入声明与校验
# ---------------------------------------------------------------------------


async def test_required_inputs_enforced_at_run(registered: Any) -> None:
    """required_inputs 声明的键在 run() 前校验：缺参 ValueError，不跑任何节点。"""
    ran = {"n": 0}

    async def only(ctx: dict[str, Any]) -> str:
        ran["n"] += 1
        return ctx["query"]

    registered("t_only", only, "function")
    dag = load_dag({"nodes": {"only": {"type": "t_only"}}, "required_inputs": ["query"]})

    with pytest.raises(ValueError, match="缺少必填输入参数: query"):
        await dag.run()
    assert ran["n"] == 0  # 缺参时节点零执行

    results = await dag.run(inputs={"query": "hello"})
    assert results["only"].output == "hello"


def test_required_inputs_overlap_defaults_rejected() -> None:
    """必填键同时声明默认值 → 配置错误（必填就不该有默认）。"""
    with pytest.raises(ValueError, match="重叠"):
        load_dag(
            {
                "nodes": {"only": {"type": "cfg_fetch"}},
                "required_inputs": ["query"],
                "inputs": {"query": "有默认值"},
            }
        )


def test_required_inputs_bad_type_rejected() -> None:
    with pytest.raises(ValueError, match="非空字符串列表"):
        load_dag({"nodes": {"only": {"type": "cfg_fetch"}}, "required_inputs": "query"})


def test_input_keys_clash_node_names_rejected() -> None:
    """默认/必填输入键与节点名冲突 → load_dag 拒绝（ctx 命名空间共享）。"""
    with pytest.raises(ValueError, match="冲突"):
        load_dag(
            {
                "nodes": {"only": {"type": "cfg_fetch"}},
                "inputs": {"only": 1},
            }
        )


# ---------------------------------------------------------------------------
# params 富声明 — label/description/default/required，与简式归一化
# ---------------------------------------------------------------------------


def test_parse_params_rich_form() -> None:
    """params 富声明 → 统一参数行 + 派生 default_inputs/required_inputs。"""
    rows, defaults, required = parse_params(
        {
            "params": {
                "query": {"required": True, "label": "查询词", "description": "要检索的内容"},
                "topic": {"default": "默认主题", "label": "主题"},
                "limit": {"default": 5},  # 可选、无 label → label 退化为键名
                "body": {"required": True, "multiline": True},  # 多行文本（前端 textarea）
            }
        }
    )
    assert rows == [
        {"name": "query", "label": "查询词", "description": "要检索的内容", "default": None, "has_default": False, "required": True, "multiline": False},
        {"name": "topic", "label": "主题", "description": None, "default": "默认主题", "has_default": True, "required": False, "multiline": False},
        {"name": "limit", "label": "limit", "description": None, "default": 5, "has_default": True, "required": False, "multiline": False},
        {"name": "body", "label": "body", "description": None, "default": None, "has_default": False, "required": True, "multiline": True},
    ]
    assert defaults == {"topic": "默认主题", "limit": 5}
    assert required == ["query", "body"]


def test_params_multiline_bad_type_rejected() -> None:
    with pytest.raises(ValueError, match="multiline 必须是布尔值"):
        parse_params({"params": {"b": {"multiline": "yes"}}})


def test_parse_params_legacy_form_rows() -> None:
    """inputs/required_inputs 简式 → 同样产出参数行（label=键名、无说明）。"""
    rows, defaults, required = parse_params(
        {"required_inputs": ["query"], "inputs": {"topic": "默认主题"}}
    )
    assert rows == [
        {"name": "query", "label": "query", "description": None, "default": None, "has_default": False, "required": True, "multiline": False},
        {"name": "topic", "label": "topic", "description": None, "default": "默认主题", "has_default": True, "required": False, "multiline": False},
    ]
    assert defaults == {"topic": "默认主题"} and required == ["query"]


async def test_params_rich_form_runs(registered: Any) -> None:
    """params 声明的必填/默认与简式语义一致：run 前校验、默认值进 ctx。"""

    async def search(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"query": ctx["query"], "topic": ctx.get("topic")}

    registered("t_search", search, "function")
    dag = load_dag(
        {
            "nodes": {"search": {"type": "t_search"}},
            "params": {
                "query": {"required": True, "label": "查询词"},
                "topic": {"default": "默认主题"},
            },
        }
    )
    assert dag.default_inputs == {"topic": "默认主题"}
    assert dag.required_inputs == ["query"]

    with pytest.raises(ValueError, match="缺少必填输入参数: query"):
        await dag.run()
    # run(inputs=...) 整体替换默认值；合并语义在 orchestrator（runtime 覆盖默认）
    results = await dag.run(inputs={**dag.default_inputs, "query": "洛伦佐"})
    assert results["search"].output == {"query": "洛伦佐", "topic": "默认主题"}


def test_params_required_with_default_rejected() -> None:
    with pytest.raises(ValueError, match="必填参数不应有默认值"):
        parse_params({"params": {"q": {"required": True, "default": "x"}}})


def test_params_unknown_field_rejected() -> None:
    with pytest.raises(ValueError, match="不支持的字段"):
        parse_params({"params": {"q": {"type": "string"}}})


def test_params_mixed_with_legacy_rejected() -> None:
    """params 与 inputs/required_inputs 混用 → 二义，拒绝。"""
    with pytest.raises(ValueError, match="不能与 inputs/required_inputs 混用"):
        parse_params({"params": {"q": {"required": True}}, "inputs": {"topic": "t"}})


# ---------------------------------------------------------------------------
# review 审核视图 — human 节点声明审核者看什么
# ---------------------------------------------------------------------------


def test_parse_review_forms() -> None:
    """列表/裸字符串映射/富映射归一化为 {key: label}；缺省 → None（全量）。"""
    assert parse_review(None) is None
    assert parse_review(["title"]) == {"title": "title"}
    assert parse_review({"title": "标题"}) == {"title": "标题"}
    assert parse_review({"title": {"label": "标题"}, "content": {}}) == {"title": "标题", "content": "content"}


def test_parse_review_rejects() -> None:
    with pytest.raises(ValueError, match="非空字符串键列表"):
        parse_review(["", 1])
    with pytest.raises(ValueError, match="不能为空映射"):
        parse_review({})
    with pytest.raises(ValueError, match="不支持的字段"):
        parse_review({"t": {"format": "text"}})
    with pytest.raises(ValueError, match="必须是键列表或映射"):
        parse_review("title")


async def test_human_review_view_payload(registered: Any) -> None:
    """声明 review：payload 只含声明键 + 首位 _review 标签；运行时缺失的键置 None。"""
    seen: dict[str, Any] = {}

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen.update(payload)
        return {"approve": True}

    async def work(ctx: dict[str, Any]) -> str:
        return "done"

    registered("t_work", work, "function")
    dag = load_dag(
        {
            # opt：可选参数、无默认值 → 本次运行不提供，审核视图里应为 None 而非消失
            "params": {"opt": {}},
            "nodes": {
                "work": {"type": "t_work"},
                "gate": {
                    "kind": "human",
                    "depends_on": ["work"],
                    "prompt": "重点核对工作成果",
                    "review": {"work": {"label": "工作成果"}, "opt": {"label": "可选参数"}},
                },
            },
        },
        approver=approver,
    )
    results = await dag.run()
    assert seen == {
        "_prompt": "重点核对工作成果",
        "_review": {"work": "工作成果", "opt": "可选参数"},
        "work": "done",
        "opt": None,
    }
    # 通过后节点输出的 payload 与审核者看到的一致（决策记录）
    assert results["gate"].output["payload"]["work"] == "done"


def test_review_unknown_key_rejected() -> None:
    """review 键不在参数键/节点名里（拼写错误）→ 载入时拒绝，而非审核时静默 None。"""
    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True}

    with pytest.raises(ValueError, match="review 引用了未声明的键 ttile"):
        load_dag(
            {
                "nodes": {
                    "work": {"type": "cfg_fetch"},
                    "gate": {"kind": "human", "depends_on": ["work"], "review": ["ttile"]},
                },
            },
            approver=approver,
        )


def test_review_param_key_allowed() -> None:
    """review 键可以是参数键（含无默认值的可选参数）——校验用参数行全集。"""
    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True}

    dag = load_dag(
        {
            "params": {"q": {"required": True}, "opt": {}},
            "nodes": {
                "gate": {"kind": "human", "depends_on": [], "review": ["q", "opt"]},
            },
        },
        approver=approver,
    )
    assert dag.human_nodes[0].name == "gate"
