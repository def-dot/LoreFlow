"""声明式配置层 — load_dag 解析与校验"""

from typing import Any, Literal

import pytest

from app.core.config import settings
from app.engine import DAG, NodeStatus, RetryPolicy, load_dag
from app.engine.declarative import read_yaml
from app.engine.resolve import parse_retry
from app.engine.validate import (
    validate_config,
    validate_inputs,
    validate_nodes,
    validate_params,
)
from app.registry import REGISTRY, NodeType


@pytest.fixture
def registered() -> Any:
    """临时注册测试用的节点函数，测试结束后自动撤销（直接读写 REGISTRY）。"""
    added: list[str] = []

    def _reg(name: str, func: Any) -> None:
        REGISTRY[name] = NodeType(name=name, func=func, label=name, description=name)
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
            "review": {"type": "human", "depends_on": ["data"], "prompt": "check it"},
        },
    }
    dag = load_dag(config, approver=approver)
    results = await dag.run()
    assert results["review"].status == NodeStatus.COMPLETED


async def test_load_dag_human_with_condition() -> None:
    """human 节点支持 condition 表达式 —— False 时跳过审核，approver 不被调用。"""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((node_name, payload))
        return {"approve": True}

    config = {
        "inputs": {"approved": {"default": False}},
        "nodes": {
            "data": {"type": "cfg_fetch"},
            "review": {"type": "human", "depends_on": ["data"], "condition": "$approved == true"},
        },
    }
    dag = load_dag(config, approver=approver)
    results = await dag.run()
    assert results["review"].status == NodeStatus.SKIPPED
    assert calls == []


async def test_load_dag_loop(registered: Any) -> None:
    async def tick(ctx: dict[str, Any]) -> int:
        return ctx.get("tick", 0) + 1

    registered("tick", tick)

    config = {
        "nodes": {
            "batch": {
                "type": "loop",
                "body": {"tick": {"type": "tick"}},
                "condition": "$iteration < 1",
                "max_iterations": 2,
            },
        },
    }
    dag = load_dag(config)
    results = await dag.run()
    assert results["batch"].status == NodeStatus.COMPLETED


def test_registry_only_lookup() -> None:
    # type 只能引用注册表中的名字，没有 functions 参数可传
    dag = load_dag({"nodes": {"a": {"type": "cfg_fetch"}}})
    assert "a" in dag.nodes

    # 点路径不是注册名字，同样被拒绝
    with pytest.raises(ValueError, match="未注册"):
        load_dag({"nodes": {"b": {"type": "app.registry.demo.cfg_fetch"}}})


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
    with pytest.raises(ValueError, match="不支持的字段"):
        load_dag({"nodes": {"a": {"kind": "human"}}})  # kind 已废除，只有 type
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
        load_dag({"nodes": {"l": {"type": "loop", "condition": "$x"}}})
    with pytest.raises(ValueError, match="需要 'condition'"):
        load_dag({"nodes": {"l": {"type": "loop", "body": {"t": {"type": "cfg_fetch"}}}}})
    with pytest.raises(ValueError, match="必须提供 approver"):
        load_dag({"nodes": {"r": {"type": "human"}}})
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
    """params 声明的必填键：run() 对生效输入强制契约——缺必填开跑前即 ValueError。"""
    ran = {"n": 0}

    async def only(ctx: dict[str, Any]) -> str:
        ran["n"] += 1
        return ctx["query"]

    registered("t_only", only)
    dag = load_dag({"nodes": {"only": {"type": "t_only"}}, "inputs": {"query": {"required": True}}})

    assert dag.validate() == []
    assert validate_inputs({}, dag.params) == ["必填参数缺失或为空: query"]

    with pytest.raises(ValueError, match="必填参数缺失或为空"):
        await dag.run()

    results = await dag.run(inputs={"query": "hello"})
    assert results["only"].output == "hello"


async def test_required_with_default_fills_when_omitted(registered: Any) -> None:
    """必填键同时声明 default：引擎层 default 是真默认值——run() 不传参
    时顶班跑通；显式空输入（inputs={}）不回退 default、仍算缺失。显式性
    只在 API 边界强制（create_run 校验原始 inputs），见 test_api。"""
    ran = {"n": 0}

    async def only(ctx: dict[str, Any]) -> str:
        ran["n"] += 1
        return ctx["query"]

    registered("t_required_default", only)
    dag = load_dag(
        {"nodes": {"only": {"type": "t_required_default"}},
         "inputs": {"query": {"required": True, "default": "建议值"}}}
    )
    assert dag.required_inputs == ["query"]
    assert dag.default_inputs == {"query": "建议值"}  # 必填键的 default 也进回填视图

    results = await dag.run()  # 不传参 → default 顶班
    assert results["only"].output == "建议值"
    assert ran["n"] == 1

    # 显式空 = 未提供，不回退 default（create_run 校验原始 inputs 时拦截）
    assert validate_inputs({}, dag.params) == ["必填参数缺失或为空: query"]

    results = await dag.run(inputs={"query": "显式值"})
    assert results["only"].output == "显式值"
    assert ran["n"] == 2


def test_required_inputs_bad_type_rejected() -> None:
    """required 布尔值 → 类型校验。"""
    errors = validate_params({"inputs": {"query": {"required": "yes"}}})
    assert errors == ["参数 'query': required 必须是布尔值"]


async def test_required_inputs_empty_values_rejected(registered: Any) -> None:
    """必填校验不看 falsy：null/空串/空白串算未提供，0/False 是合法值。"""

    async def only(ctx: dict[str, Any]) -> Any:
        return ctx["count"]

    registered("t_echo_count", only)
    dag = load_dag(
        {"nodes": {"echo": {"type": "t_echo_count"}},
         "inputs": {"count": {"required": True}}}
    )

    for bad in (None, "", "   "):
        assert validate_inputs({"count": bad}, dag.params) == ["必填参数缺失或为空: count"]

    for good in (0, False):
        results = await dag.run(inputs={"count": good})
        assert results["echo"].output == good  # 0/False 是填了的合法值


async def test_undeclared_params_reject_all_inputs(registered: Any) -> None:
    """输入键必须是声明参数的子集：声明了 params → inputs ⊆ 声明键；
    未声明 params → 白名单为空，任何输入键都算未声明（loop body
    不受影响——loop_func 直接驱动执行器，不走 run() 的输入契约）。"""

    async def echo(ctx: dict[str, Any]) -> Any:
        return ctx["extra"]

    registered("t_echo_extra", echo)

    # 声明了 params 契约：extra 未声明 → 拒
    declared = load_dag(
        {"nodes": {"echo": {"type": "t_echo_extra"}},
         "inputs": {"q": {"required": True}}}
    )
    inputs = {"q": "ok", "extra": 1}
    assert validate_inputs(inputs, declared.params) == ["未声明的参数键: extra"]

    # 未声明 params：白名单为空，q/extra 都是未声明的
    free = load_dag({"nodes": {"echo": {"type": "t_echo_extra"}}})
    assert validate_inputs(inputs, free.params) == ["未声明的参数键: extra, q"]

    # 未声明 params = 自由上下文种子：run() 不设白名单，原样进 ctx
    results = await free.run(inputs={"extra": 1})
    assert results["echo"].output == 1


def test_input_keys_clash_node_names_rejected() -> None:
    """参数键与节点名冲突 → load_dag 拒绝（ctx 命名空间共享）。"""
    with pytest.raises(ValueError, match="参数键与节点名冲突"):
        load_dag(
            {
                "nodes": {"only": {"type": "cfg_fetch"}},
                "inputs": {"only": {"required": True}},
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
    dag = load_dag({"nodes": {"only": {"type": "cfg_fetch"}}, "inputs": params})
    assert dag.params == params  # 声明原样保留
    assert dag.default_inputs == {"topic": "默认主题", "limit": 5}
    assert dag.required_inputs == ["query", "body"]


def test_params_multiline_bad_type_rejected() -> None:
    errors = validate_params({"inputs": {"b": {"multiline": "yes"}}})
    assert errors == ["参数 'b': multiline 必须是布尔值"]


async def test_params_rich_form_runs(registered: Any) -> None:
    """params 声明的必填/默认与简式语义一致：run 前校验、默认值进 ctx。"""

    async def search(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"query": ctx["query"], "topic": ctx.get("topic")}

    registered("t_search", search)
    dag = load_dag(
        {
            "nodes": {"search": {"type": "t_search"}},
            "inputs": {
                "query": {"required": True, "label": "查询词"},
                "topic": {"default": "默认主题"},
            },
        }
    )
    assert dag.default_inputs == {"topic": "默认主题"}
    assert dag.required_inputs == ["query"]
    assert validate_inputs({}, dag.params) == ["必填参数缺失或为空: query"]

    # run(inputs=...) 整体替换默认值；合并语义在 orchestrator（runtime 覆盖默认）
    results = await dag.run(inputs={**dag.default_inputs, "query": "洛伦佐"})
    assert results["search"].output == {"query": "洛伦佐", "topic": "默认主题"}


def test_params_unknown_field_rejected() -> None:
    errors = validate_params({"inputs": {"q": {"type": "string"}}})
    assert errors == ["参数 'q': 不支持的字段 ['type']"]


def test_params_collects_all_errors() -> None:
    """多个参数错误一次性全部返回，而非遇错即抛。"""
    errors = validate_params({"inputs": {
        "q": {"bogus": 1},
        "r": {"required": "yes"},
        "s": "not-a-mapping",
    }})
    assert len(errors) == 3
    assert any("不支持的字段" in e for e in errors)
    assert any("required 必须是布尔值" in e for e in errors)
    assert any("定义必须是映射" in e for e in errors)


def test_params_node_name_clash_rejected() -> None:
    """参数键与节点名冲突应被拒绝"""
    assert validate_params(
        {"inputs": {"query": {"required": True}}, "nodes": {"query": {}}}
    ) == ["输入参数键与节点名冲突: query"]


def test_params_no_clash_accepted() -> None:
    """参数键与节点名无冲突时通过"""
    assert validate_params(
        {"inputs": {"query": {"required": True}}, "nodes": {"fetch": {}}}
    ) == []


# ---------------------------------------------------------------------------
# review 审核视图 — human 节点声明审核者看什么
# ---------------------------------------------------------------------------


def test_review_declaration_not_validated() -> None:
    """review 卡片声明不校验内容与来源（运行期兜底：字段取不到显示「未提供」）。"""
    # 拼错键 / 参数键 / 上游节点名 / 协议键——载入期一律放行
    assert validate_config({"inputs": {"title": {}}, "nodes": {
        "work": {"type": "cfg_fetch"},
        "gate": {"type": "human", "depends_on": ["work"],
                 "inputs": {"_review": {"title": "标题", "work": "产出", "ttile": "拼错", "approve": "协议键"}}},
    }}) == []


# ---------------------------------------------------------------------------
# validate_nodes — 节点声明校验
# ---------------------------------------------------------------------------


def test_validate_nodes_accepts() -> None:
    """validate_nodes 接受合法的节点配置"""
    # node 类型节点
    assert validate_nodes({"nodes": {"a": {"type": "cfg_fetch"}}}) == []
    assert validate_nodes({"nodes": {"a": {"type": "cfg_fetch", "depends_on": []}}}) == []

    # human 类型节点
    assert validate_nodes({"nodes": {"a": {"type": "human", "prompt": "审核"}}}) == []
    # human 节点 review - 键必须在 params 或 nodes 中
    assert validate_nodes({
        "nodes": {"a": {"type": "human", "prompt": "审核", "review": {"title": {"label": "标题"}}}},
        "inputs": {"title": {}},
    }) == []


def test_validate_nodes_accepts_loop() -> None:
    """loop 节点的 condition 是表达式（iteration 在循环谓词视图可用）"""
    assert validate_nodes({
        "nodes": {"a": {"type": "loop", "body": {"b": {"type": "cfg_fetch"}}, "condition": "$iteration < 3"}}
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

    # kind 已废除：报不支持字段 + 缺 type（字段检查先于 membership）
    assert validate_nodes({"nodes": {"a": {"kind": "quantum"}}}) == [
        "节点 'a': 不支持的字段 ['kind']",
        "节点 'a': 需要 'type'（函数键）",
    ]

    # 不支持的字段
    assert validate_nodes({"nodes": {"a": {"type": "cfg_fetch", "bogus": 1}}}) == [
        "节点 'a'（cfg_fetch）: 不支持的字段 ['bogus']"
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

    # loop 类型缺少 body / condition、condition 引用未知键（condition 校验先于 body 检查）
    assert validate_nodes({"nodes": {"a": {"type": "loop", "condition": "$x"}}}) == [
        "节点 'a': condition 引用的 'x' 不是参数键或上游依赖节点",
        "循环节点 'a': 需要非空的 'body' 映射",
    ]
    assert validate_nodes({"nodes": {"a": {"type": "loop", "body": {"b": {"type": "cfg_fetch"}}}}}) == [
        "循环节点 'a': 需要 'condition' 表达式"
    ]

    # human 节点 review 校验失败（带节点名前缀）
    assert validate_nodes({
        "nodes": {"a": {"type": "human", "review": {"a": {"label": None}}}},
        "inputs": {"a": {}},
    }) == ["审核节点 'a': review 字段 'a': label 必须是字符串"]


def test_validate_nodes_collects_errors_across_nodes() -> None:
    """不同节点的错误一次性全部返回。"""
    errors = validate_nodes({
        "nodes": {
            "a": {},                                   # 缺 type
            "b": {"type": "cfg_fetch", "bogus": 1},    # 不支持的字段
            "c": {"type": "cfg_fetch"},                # 合法
        }
    })
    assert len(errors) == 2
    assert any("需要 'type'" in e for e in errors)
    assert any("不支持的字段" in e for e in errors)


# ---------------------------------------------------------------------------
# validate_config — 完整配置校验
# ---------------------------------------------------------------------------


def test_validate_config_accepts() -> None:
    """validate_config 接受合法的完整配置（结构检查要求至少一个节点）"""
    # 只有 nodes（params 可选）
    assert validate_config({"nodes": {"a": {"type": "cfg_fetch"}}}) == []

    # 完整配置
    assert validate_config({
        "inputs": {"query": {"required": True}},
        "nodes": {"fetch": {"type": "cfg_fetch"}},
    }) == []


def test_validate_config_rejects_bad_structure() -> None:
    """图结构错误：空流水线 / 依赖缺失 / 循环依赖（loop body 递归同查）"""
    # nodes 是必填键：未声明（含只声明 params）与空映射都报错
    assert validate_config({}) == ["流水线至少需要一个节点"]
    assert validate_config({"inputs": {"q": {"required": True}}}) == [
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
    assert validate_config({"nodes": {
        "l": {
            "type": "loop",
            "condition": "$iteration < 3",
            "body": {"b": {"type": "cfg_fetch", "depends_on": ["ghost"]}},
        },
    }}) == ["循环节点 'l': 节点 'b' 依赖的 'ghost' 不在 DAG 中"]


def test_validate_config_rejects_param_node_clash() -> None:
    """validate_config 拒绝参数键与节点名冲突"""
    config = {
        "inputs": {"query": {"required": True}},
        "nodes": {"query": {"type": "cfg_fetch"}},
    }
    assert validate_config(config) == ["输入参数键与节点名冲突: query"]


def test_param_node_clash_checked_at_engine() -> None:
    """程序化 DAG 不走 validate_params——冲突由 DAG.validate 兜底拦截。"""
    dag = DAG("clash", params={"query": {"required": True}})

    @dag.node("query")
    async def query(ctx: dict[str, Any]) -> str:
        return "output"

    # validate 只查结构（不含输入），冲突项天然隔离
    assert dag.validate() == ["输入参数键与节点名冲突: query"]


def test_param_spec_shape_checked_at_engine() -> None:
    """程序化 DAG 的 params 形状由 DAG.validate 兜底——畸形 spec 不再让
    required_inputs 的派生视图 AttributeError，而是清晰的校验消息。"""
    dag = DAG("bad_spec", params={"q": "不是字典"})

    @dag.node("only")
    async def only(ctx: dict[str, Any]) -> str:
        return "x"

    assert dag.validate() == ["参数 'q': 定义必须是映射(dict)，实际是 str"]


def test_validate_config_collects_all_errors() -> None:
    """params 与 nodes 的错误一次性全部返回（load_dag 抛出时含全部信息）"""
    config = {
        "inputs": {
            "q": {"bogus": 1},          # 不支持的字段
            "r": {"required": "yes"},   # required 类型错误
        },
        "nodes": {
            "a": {},                                 # 缺 type
            "b": {"type": "cfg_fetch", "bogus": 1},  # 不支持的字段
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


async def test_human_review_view_payload(registered: Any) -> None:
    """声明 review：payload 只含声明键 + 首位 _review 富映射；运行时缺失的键置 None。"""
    seen: dict[str, Any] = {}

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        seen.update(payload)
        return {"approve": True}

    async def work(ctx: dict[str, Any]) -> str:
        return "done"

    registered("t_work", work)
    dag = load_dag(
        {
            # opt：可选参数、无默认值 → 本次运行不提供，审核视图里应为 None 而非消失
            "inputs": {"opt": {}},
            "nodes": {
                "work": {"type": "t_work"},
                "gate": {
                    "type": "human",
                    "depends_on": ["work"],
                    "description": "重点核对工作成果",
                    "inputs": {
                        "_review": {"work": {"label": "工作成果"}, "opt": {"label": "可选参数"}},
                    },
                },
            },
        },
        approver=approver,
    )
    results = await dag.run()
    assert seen == {
        # _review 原样携带声明富映射（前端按 {key: {label: 文本}} 取标签）
        "_review": {"work": {"label": "工作成果"}, "opt": {"label": "可选参数"}},
        "work": "done",
        "opt": None,
    }
    # 通过后节点输出的 payload 与审核者看到的一致（决策记录）
    assert results["gate"].output["payload"]["work"] == "done"


def test_review_unknown_key_not_checked() -> None:
    """review 键拼错不拦截：载入照常，审核卡片上该字段显示「未提供」。"""
    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True}

    dag = load_dag(
        {
            "inputs": {},
            "nodes": {
                "work": {"type": "cfg_fetch"},
                "gate": {
                    "type": "human", "depends_on": ["work"],
                    "inputs": {"_review": {"ttile": "标题"}},
                },
            },
        },
        approver=approver,
    )
    assert dag.human_nodes[0].name == "gate"


def test_review_param_key_allowed() -> None:
    """review 键可以是参数键（含无默认值的可选参数）——校验用参数声明全集。"""
    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {"approve": True}

    dag = load_dag(
        {
            "inputs": {"q": {"required": True}, "opt": {}},
            "nodes": {
                "gate": {
                    "type": "human", "depends_on": [],
                    "inputs": {"_review": {"q": "查询", "opt": "可选参数"}},
                },
            },
        },
        approver=approver,
    )
    assert dag.human_nodes[0].name == "gate"


# ---------------------------------------------------------------------------
# condition 表达式 — 声明层分流与载入期校验
# ---------------------------------------------------------------------------


async def test_condition_expression_runs_and_skips() -> None:
    """等值表达式按 inputs 值分流：pick == a / pick == b 各自命中。"""
    config = {
        "inputs": {"pick": {}},
        "nodes": {
            "a": {"type": "cfg_fetch", "condition": "$pick == a"},
            "b": {"type": "cfg_fetch", "condition": "$pick == b"},
        },
    }
    results = await load_dag(config).run(inputs={"pick": "a"})
    assert results["a"].status is NodeStatus.COMPLETED
    assert results["b"].status is NodeStatus.SKIPPED

    results = await load_dag(config).run(inputs={"pick": "b"})
    assert results["a"].status is NodeStatus.SKIPPED
    assert results["b"].status is NodeStatus.COMPLETED


async def test_condition_expression_on_wired_key() -> None:
    """条件在接线视图上求值：$node.field 点路径取字段后直接比较。"""
    config = {
        "nodes": {
            "data": {"type": "cfg_fetch"},
            "gold": {
                "type": "cfg_fetch",
                "depends_on": ["data"],
                "condition": "$tier == gold",
                "inputs": {"tier": "$data.title"},  # title = "DAG Flow v0.1" ≠ gold
            },
        },
    }
    results = await load_dag(config).run()
    assert results["gold"].status is NodeStatus.SKIPPED


async def test_condition_expression_dollar_reference() -> None:
    """$ 引用前缀：condition 直接引用上游输出字段，无需 inputs 接线中转。"""
    config = {
        "nodes": {
            "data": {"type": "cfg_fetch"},
            "gold": {
                "type": "cfg_fetch",
                "depends_on": ["data"],
                "condition": "$data.title == 'DAG Flow v0.1'",
            },
        },
    }
    results = await load_dag(config).run()
    assert results["gold"].status is NodeStatus.COMPLETED

    config["nodes"]["gold"]["condition"] = "$data.title == nope"
    results = await load_dag(config).run()
    assert results["gold"].status is NodeStatus.SKIPPED


async def test_condition_boolean_constants() -> None:
    """condition: true/false 布尔常量 —— false 当开关恒跳过，true 恒执行。"""
    config = {
        "nodes": {
            "off": {"type": "cfg_fetch", "condition": False},
            "on": {"type": "cfg_fetch", "condition": True},
        },
    }
    assert validate_config(config) == []
    results = await load_dag(config).run()
    assert results["off"].status is NodeStatus.SKIPPED
    assert results["on"].status is NodeStatus.COMPLETED

    # loop 的 condition: false 合法（body 一轮不跑）；condition: null 等同未声明
    assert validate_config({"nodes": {
        "l": {"type": "loop", "body": {"t": {"type": "cfg_fetch"}}, "condition": False},
    }}) == []
    assert validate_config({"nodes": {
        "a": {"type": "cfg_fetch", "condition": None},
    }}) == []


def test_condition_refs_not_validated() -> None:
    """condition 只查语法，引用键不做来源校验——求值在接线视图上进行，
    loop 注入的 iteration 等运行期键无法静态枚举。"""
    # 未声明依赖的节点 / 拼错的键都不报（运行期取 None 恒 False 跳过）
    assert validate_config({"nodes": {
        "甲": {"type": "cfg_fetch"},
        "乙": {"type": "cfg_fetch", "condition": "$甲.title == x"},  # 未声明依赖
    }}) == []
    assert validate_config({"nodes": {
        "甲": {"type": "cfg_fetch", "condition": "$iteration < 3"},
    }, "inputs": {}}) == []


# ---------------------------------------------------------------------------
# 真实示例流水线 02_condition.yaml — 级联 skip 与 final_answer 汇合
# ---------------------------------------------------------------------------


async def _run_condition_yaml(
    monkeypatch: pytest.MonkeyPatch, prompt: str, classify_raw: str = ""
) -> dict[str, Any]:
    """桩掉 Ollama 跑真实 02_condition.yaml：classify_raw 控制 llm_classify 的
    模型输出（空串 = 预期不触达模型，如「人工」关键词短路）。"""
    from app.registry import llm as llm_mod

    async def fake_ollama(model: str, messages: list[dict[str, str]], fmt: Any = None) -> str:
        assert classify_raw, "本用例不应触达 Ollama"
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "意图分类器" in system:
            return classify_raw
        if "知识库问答助手" in system:
            return "知识库支路答复"
        return "闲聊支路答复"

    monkeypatch.setattr(llm_mod, "_ollama_chat", fake_ollama)
    _, config = read_yaml(settings.PIPELINES_DIR / "02_condition.yaml")
    dag = load_dag(config)
    return await dag.run(inputs={"prompt": prompt})


async def test_condition_yaml_chat_branch_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat 支路：rag 支路整条级联跳过，final_answer 汇合取 chat 答复。"""
    results = await _run_condition_yaml(monkeypatch, "你好呀", classify_raw="chat")
    assert results["intent_recognition"].status is NodeStatus.COMPLETED
    assert results["llm_chat"].status is NodeStatus.COMPLETED
    assert results["rag_retrieve"].status is NodeStatus.SKIPPED
    assert results["llm_rag_reply"].status is NodeStatus.UPSTREAM_SKIPPED
    assert results["final_answer"].status is NodeStatus.COMPLETED
    assert results["final_answer"].output == {"branch": "llm_chat", "answer": "闲聊支路答复"}


async def test_condition_yaml_rag_branch_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """rag 支路：chat 支路条件跳过，final_answer 汇合取 rag 答复。"""
    results = await _run_condition_yaml(monkeypatch, "北境要塞是什么", classify_raw="rag")
    assert results["llm_chat"].status is NodeStatus.SKIPPED
    assert results["rag_retrieve"].status is NodeStatus.COMPLETED
    assert results["llm_rag_reply"].status is NodeStatus.COMPLETED
    assert results["final_answer"].status is NodeStatus.COMPLETED
    assert results["final_answer"].output == {"branch": "llm_rag_reply", "answer": "知识库支路答复"}


async def test_condition_yaml_human_keyword_cascades_to_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """「人工」关键词短路判 human：两条支路全跳过，汇合节点级联跳过。
    （human_service 支路已从示例移除，转人工意图暂无落点——全跳过时
    final_answer 不执行，run 空手完成。）"""
    results = await _run_condition_yaml(monkeypatch, "我要找人工客服")
    assert results["llm_chat"].status is NodeStatus.SKIPPED
    assert results["llm_rag_reply"].status is NodeStatus.UPSTREAM_SKIPPED
    assert results["final_answer"].status is NodeStatus.UPSTREAM_SKIPPED


def test_condition_expression_validation() -> None:
    """表达式的载入期校验：类型、语法、引用键存在性、loop 的 iteration。"""
    # 旧的单键映射形式（函数调用）不再支持
    assert validate_config({"nodes": {
        "a": {"type": "cfg_fetch", "condition": {"t_eq": "a"}},
    }}) == [
        "节点 'a': condition 必须是非空表达式字符串"
        "（如 $intent == chat / $merge / not $flag），实际是 {'t_eq': 'a'}"
    ]

    # 语法错误：缺键
    assert validate_config({"nodes": {
        "a": {"type": "cfg_fetch", "condition": "== chat"},
    }}) == [
        "节点 'a': 条件表达式 '== chat' 无法解析"
        "（写法如 ``$intent == chat``、``$merge``、``not $flag``）"
    ]

    # 引用键不做来源校验（求值在接线视图上，loop 注入键无法静态枚举）：
    # 拼错 / $ 前缀 / iteration 均不报，运行期取 None 恒 False 跳过
    assert validate_config({"nodes": {
        "a": {"type": "cfg_fetch", "condition": "$pick2 == a"},
    }}) == []
    assert validate_config({"nodes": {
        "a": {"type": "cfg_fetch", "condition": "$typo.field == x"},
    }}) == []
    assert validate_config({"nodes": {
        "a": {"type": "cfg_fetch", "condition": "$iteration < 3"},
    }}) == []
    assert validate_config({"nodes": {
        "data": {"type": "cfg_fetch"},
        "gold": {"type": "cfg_fetch", "depends_on": ["data"], "condition": "$data.title == gold"},
    }}) == []
