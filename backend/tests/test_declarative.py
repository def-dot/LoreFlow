"""声明式配置层 — load_dag 解析与校验"""

from typing import Any, Literal

import pytest

from app.engine import DAG, NodeStatus, RetryPolicy, load_dag
from app.engine.declarative import (
    _validate_review,
    validate_config,
    validate_nodes,
    validate_params,
)
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
    with pytest.raises(ValueError, match="类型函数 'no_such_fn' 未注册"):
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
    """params 声明的必填键在 run() 前校验：缺参 ValueError，不跑任何节点。"""
    ran = {"n": 0}

    async def only(ctx: dict[str, Any]) -> str:
        ran["n"] += 1
        return ctx["query"]

    registered("t_only", only, "function")
    dag = load_dag({"nodes": {"only": {"type": "t_only"}}, "params": {"query": {"required": True}}})

    with pytest.raises(ValueError, match="缺少必填输入参数: query"):
        await dag.run()
    assert ran["n"] == 0  # 缺参时节点零执行

    results = await dag.run(inputs={"query": "hello"})
    assert results["only"].output == "hello"


async def test_required_with_default_fills_when_missing(registered: Any) -> None:
    """必填键可同时声明 default：default 与 required 无关，
    缺显式输入时 default 顶班，显式输入覆盖 default。"""
    ran = {"n": 0}

    async def only(ctx: dict[str, Any]) -> str:
        ran["n"] += 1
        return ctx["query"]

    registered("t_required_default", only, "function")
    dag = load_dag(
        {"nodes": {"only": {"type": "t_required_default"}},
         "params": {"query": {"required": True, "default": "建议值"}}}
    )
    assert dag.required_inputs == ["query"]
    assert dag.default_inputs == {"query": "建议值"}  # default 与 required 无关

    results = await dag.run()  # 未显式传入 → default 顶班
    assert ran["n"] == 1
    assert results["only"].output == "建议值"

    results = await dag.run(inputs={"query": "显式值"})
    assert results["only"].output == "显式值"


def test_required_inputs_bad_type_rejected() -> None:
    """required 布尔值 → 类型校验。"""
    errors = validate_params({"params": {"query": {"required": "yes"}}})
    assert errors == ["参数 'query': required 必须是布尔值"]


def test_input_keys_clash_node_names_rejected() -> None:
    """参数键与节点名冲突 → load_dag 拒绝（ctx 命名空间共享）。"""
    with pytest.raises(ValueError, match="参数键与节点名冲突"):
        load_dag(
            {
                "nodes": {"only": {"type": "cfg_fetch"}},
                "params": {"only": {"required": True}},
            }
        )


# ---------------------------------------------------------------------------
# params 富声明 — label/description/default/required，与简式归一化
# ---------------------------------------------------------------------------


def test_params_rich_form() -> None:
    """params 富声明原样进 DAG；默认值/必填键是从声明派生的只读视图
    （前端参数行由展示层从同一声明派生，见 services/pipelines._param_rows）。"""
    params = {
        "query": {"required": True, "label": "查询词", "description": "要检索的内容"},
        "topic": {"default": "默认主题", "label": "主题"},
        "limit": {"default": 5},  # 可选、无 label → 展示层 label 退化为键名
        "body": {"required": True, "multiline": True},  # 多行文本（前端 textarea）
    }
    dag = load_dag({"nodes": {"only": {"type": "cfg_fetch"}}, "params": params})
    assert dag.params == params  # 声明原样保留
    assert dag.default_inputs == {"topic": "默认主题", "limit": 5}
    assert dag.required_inputs == ["query", "body"]


def test_params_multiline_bad_type_rejected() -> None:
    errors = validate_params({"params": {"b": {"multiline": "yes"}}})
    assert errors == ["参数 'b': multiline 必须是布尔值"]


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


def test_params_unknown_field_rejected() -> None:
    errors = validate_params({"params": {"q": {"type": "string"}}})
    assert errors == ["参数 'q': 不支持的字段 ['type']"]


def test_params_collects_all_errors() -> None:
    """多个参数错误一次性全部返回，而非遇错即抛。"""
    errors = validate_params({
        "params": {
            "q": {"bogus": 1},
            "r": {"required": "yes"},
            "s": "not-a-mapping",
        }
    })
    assert len(errors) == 3
    assert any("不支持的字段" in e for e in errors)
    assert any("required 必须是布尔值" in e for e in errors)
    assert any("定义必须是映射" in e for e in errors)


def test_params_node_name_clash_rejected() -> None:
    """参数键与节点名冲突应被拒绝"""
    config = {
        "params": {"query": {"required": True}},
        "nodes": {"query": {"type": "cfg_fetch"}},
    }
    assert validate_params(config) == ["输入参数键与节点名冲突: query"]


def test_params_no_clash_accepted() -> None:
    """参数键与节点名无冲突时通过"""
    config = {
        "params": {"query": {"required": True}},
        "nodes": {"fetch": {"type": "cfg_fetch"}},
    }
    assert validate_params(config) == []


# ---------------------------------------------------------------------------
# review 审核视图 — human 节点声明审核者看什么
# ---------------------------------------------------------------------------


def test_validate_review_accepts() -> None:
    """_validate_review 只接受富映射格式：{key: {label: 文本}}，允许空字符串"""
    keys = {"title"}
    assert _validate_review({"title": {"label": "标题"}}, keys) == []
    assert _validate_review({"title": {"label": ""}}, keys) == []  # 空字符串允许


def test_validate_review_rejects() -> None:
    """非法格式返回错误列表"""
    assert _validate_review(None, set()) == ["review 必须是映射，实际是 NoneType"]
    assert _validate_review(["title"], set()) == ["review 必须是映射，实际是 list"]
    assert _validate_review({}, set()) == ["review 声明不能为空映射"]
    # 裸字符串值（非富映射）
    assert _validate_review({"title": "标题"}, {"title"}) == [
        "review 字段 'title': 必须是 {label: 文本} 格式，实际是 str"
    ]
    # 不支持的字段 + 缺 label（收集全部错误）
    assert _validate_review({"t": {"format": "text"}}, {"t"}) == [
        "review 字段 't': 不支持的字段 ['format']",
        "review 字段 't': label 必须是字符串",
    ]
    # label 非字符串
    assert _validate_review({"t": {"label": None}}, {"t"}) == [
        "review 字段 't': label 必须是字符串"
    ]
    # 键引用未声明
    assert _validate_review({"ghost": {"label": "x"}}, {"title"}) == [
        "review 引用了未声明的键 ghost"
    ]


# ---------------------------------------------------------------------------
# validate_nodes — 节点声明校验
# ---------------------------------------------------------------------------


def test_validate_nodes_accepts() -> None:
    """validate_nodes 接受合法的节点配置"""
    # node 类型节点
    assert validate_nodes({"nodes": {"a": {"type": "cfg_fetch"}}}) == []
    assert validate_nodes({"nodes": {"a": {"type": "cfg_fetch", "depends_on": []}}}) == []

    # human 类型节点
    assert validate_nodes({"nodes": {"a": {"kind": "human", "prompt": "审核"}}}) == []
    # human 节点 review - 键必须在 params 或 nodes 中
    assert validate_nodes({
        "nodes": {"a": {"kind": "human", "prompt": "审核", "review": {"title": {"label": "标题"}}}},
        "params": {"title": {}},
    }) == []


def test_validate_nodes_accepts_loop(registered: Any) -> None:
    """loop 节点的 condition 必须是已注册的条件函数"""
    def keep(ctx: dict[str, Any], iteration: int) -> bool:
        return False

    registered("t_keep", keep, "condition")
    assert validate_nodes({
        "nodes": {"a": {"kind": "loop", "body": {"b": {"type": "cfg_fetch"}}, "condition": "t_keep"}}
    }) == []


def test_validate_nodes_rejects() -> None:
    """validate_nodes 返回非法配置的全部错误"""
    # nodes 是必填键：未声明（None）与空映射都不行（同一句报错）
    assert validate_nodes({}) == ["流水线至少需要一个节点"]
    assert validate_nodes({"nodes": {}}) == ["流水线至少需要一个节点"]

    # nodes 不是 dict（非空非映射才走到类型分支；空列表归入"至少一个节点"）
    assert validate_nodes({"nodes": ["a"]}) == [
        "nodes 必须是映射(dict)，实际是 list"
    ]

    # 节点定义不是 dict
    assert validate_nodes({"nodes": {"a": "not_dict"}}) == [
        "节点 'a': 定义必须是映射(dict)，实际是 str"
    ]

    # 未知 kind
    assert validate_nodes({"nodes": {"a": {"kind": "quantum"}}}) == [
        "节点 'a': 未知类型 'quantum'（支持 node|human|loop）"
    ]

    # 不支持的字段
    assert validate_nodes({"nodes": {"a": {"type": "cfg_fetch", "bogus": 1}}}) == [
        "节点 'a'（node）: 不支持的字段 ['bogus']"
    ]

    # node 类型缺少 type
    assert validate_nodes({"nodes": {"a": {}}}) == [
        "节点 'a': 需要 'type'（函数键）"
    ]

    # type 未注册
    assert validate_nodes({"nodes": {"a": {"type": "no_such_fn"}}}) == [
        "节点 'a': 类型函数 'no_such_fn' 未注册"
    ]

    # depends_on 类型错误 / 依赖缺失 / 循环依赖（已并入 validate_nodes）
    assert validate_nodes({"nodes": {"a": {"type": "cfg_fetch", "depends_on": "fetch"}}}) == [
        "节点 'a': depends_on 必须是字符串列表"
    ]
    assert validate_nodes(
        {"nodes": {"a": {"type": "cfg_fetch", "depends_on": ["ghost"]}}}
    ) == ["节点 'a' 依赖的 'ghost' 不在 DAG 中"]
    assert validate_nodes({"nodes": {
        "a": {"type": "cfg_fetch", "depends_on": ["b"]},
        "b": {"type": "cfg_fetch", "depends_on": ["a"]},
    }}) == ["检测到循环依赖: a → b"]

    # loop 类型缺少 body / condition、condition 未注册（condition 注册检查先于 body 检查）
    assert validate_nodes({"nodes": {"a": {"kind": "loop", "condition": "x"}}}) == [
        "节点 'a'（loop）: 条件函数 'x' 未注册",
        "循环节点 'a': 需要非空的 'body' 映射",
    ]
    assert validate_nodes({"nodes": {"a": {"kind": "loop", "body": {"b": {"type": "cfg_fetch"}}}}}) == [
        "循环节点 'a': 需要 'condition' 函数键"
    ]

    # human 节点 review 校验失败（带节点名前缀）
    assert validate_nodes({
        "nodes": {"a": {"kind": "human", "review": {"a": {"label": None}}}},
        "params": {"a": {}},
    }) == ["审核节点 'a': review 字段 'a': label 必须是字符串"]


def test_validate_nodes_collects_errors_across_nodes() -> None:
    """不同节点的错误一次性全部返回。"""
    errors = validate_nodes({
        "nodes": {
            "a": {},                       # 缺 type
            "b": {"kind": "quantum"},      # 未知 kind
            "c": {"type": "cfg_fetch"},    # 合法
        }
    })
    assert len(errors) == 2
    assert any("需要 'type'" in e for e in errors)
    assert any("未知类型" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_config — 完整配置校验
# ---------------------------------------------------------------------------


def test_validate_config_accepts() -> None:
    """validate_config 接受合法的完整配置（结构检查要求至少一个节点）"""
    # 只有 nodes（params 可选）
    assert validate_config({"nodes": {"a": {"type": "cfg_fetch"}}}) == []

    # 完整配置
    assert validate_config({
        "params": {"query": {"required": True}},
        "nodes": {"fetch": {"type": "cfg_fetch"}},
    }) == []


def test_validate_config_rejects_bad_structure(registered: Any) -> None:
    """图结构错误：空流水线 / 依赖缺失 / 循环依赖（loop body 递归同查）"""
    # nodes 是必填键：未声明（含只声明 params）与空映射都报错
    assert validate_config({}) == ["流水线至少需要一个节点"]
    assert validate_config({"params": {"q": {"required": True}}}) == [
        "流水线至少需要一个节点"
    ]
    assert validate_config({"nodes": {}}) == ["流水线至少需要一个节点"]

    # 依赖缺失
    assert validate_config(
        {"nodes": {"a": {"type": "cfg_fetch", "depends_on": ["ghost"]}}}
    ) == ["节点 'a' 依赖的 'ghost' 不在 DAG 中"]

    # 循环依赖
    assert validate_config({"nodes": {
        "a": {"type": "cfg_fetch", "depends_on": ["b"]},
        "b": {"type": "cfg_fetch", "depends_on": ["a"]},
    }}) == ["检测到循环依赖: a → b"]

    # loop body 是独立命名空间：body 内依赖缺失带循环节点名前缀
    def keep(ctx: dict[str, Any], iteration: int) -> bool:
        return False

    registered("t_keep", keep, "condition")
    assert validate_config({"nodes": {
        "l": {
            "kind": "loop",
            "condition": "t_keep",
            "body": {"b": {"type": "cfg_fetch", "depends_on": ["ghost"]}},
        },
    }}) == ["循环节点 'l': 节点 'b' 依赖的 'ghost' 不在 DAG 中"]


def test_validate_config_rejects_param_node_clash() -> None:
    """validate_config 拒绝参数键与节点名冲突"""
    config = {
        "params": {"query": {"required": True}},
        "nodes": {"query": {"type": "cfg_fetch"}},
    }
    assert validate_config(config) == ["输入参数键与节点名冲突: query"]


def test_validate_config_collects_all_errors() -> None:
    """params 与 nodes 的错误一次性全部返回（load_dag 抛出时含全部信息）"""
    config = {
        "params": {
            "q": {"bogus": 1},          # 不支持的字段
            "r": {"required": "yes"},   # required 类型错误
        },
        "nodes": {
            "a": {},                    # 缺 type
            "b": {"kind": "quantum"},   # 未知 kind
        },
    }
    errors = validate_config(config)
    assert len(errors) == 4

    # load_dag 将全部错误合并进同一个 ValueError
    with pytest.raises(ValueError) as exc_info:
        load_dag(config)
    message = str(exc_info.value)
    assert "不支持的字段 ['bogus']" in message
    assert "required 必须是布尔值" in message
    assert "需要 'type'" in message
    assert "未知类型" in message


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
                "params": {},
                "nodes": {
                    "work": {"type": "cfg_fetch"},
                    "gate": {
                        "kind": "human", "depends_on": ["work"],
                        "review": {"ttile": {"label": "标题"}},
                    },
                },
            },
            approver=approver,
        )


def test_review_param_key_allowed() -> None:
    """review 键可以是参数键（含无默认值的可选参数）——校验用参数声明全集。"""
    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True}

    dag = load_dag(
        {
            "params": {"q": {"required": True}, "opt": {}},
            "nodes": {
                "gate": {
                    "kind": "human", "depends_on": [],
                    "review": {"q": {"label": "查询"}, "opt": {"label": "可选参数"}},
                },
            },
        },
        approver=approver,
    )
    assert dag.human_nodes[0].name == "gate"
