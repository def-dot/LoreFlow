"""声明式配置层 — Pipeline 解析与校验"""

from typing import Any, Literal

import pytest

from app.core.config import settings
from app.engine import Pipeline, Node, NodeStatus, RetryPolicy, SuspendExecution
import yaml
from app.engine.resolve import parse_retry
from helpers import validate_config
from app.registry import REGISTRY, FuncDef

@pytest.fixture
def registered() -> Any:
    """临时注册测试用的节点函数，测试结束后自动撤销（直接读写 REGISTRY）。"""
    added: list[str] = []

    def _reg(name: str, func: Any) -> None:
        REGISTRY[name] = FuncDef(name=name, func=func, label=name, description=name)
        added.append(name)

    yield _reg
    for name in added:
        REGISTRY.pop(name, None)

async def test_load_dag_from_dict_runs(registered: Any) -> None:
    async def clean(ctx: dict[str, Any]) -> str:
        return str(ctx["fetch"]["body"]).strip()

    registered("clean", clean)

    config = {
        "name": "cfg_demo",
        "nodes": {
            "fetch": {"type": "test_fetch"},
            "clean": {"type": "clean", "depends_on": ["fetch"]},
        },
    }
    dag = Pipeline(config)
    assert dag.name == "cfg_demo"
    results, _ = await dag.run()
    assert results["clean"].output == "declarative config rocks"

async def test_load_dag_with_human_node() -> None:
    config = {
        "nodes": {
            "data": {"type": "test_fetch"},
            "review": {"type": "human", "depends_on": ["data"], "prompt": "check it"},
        },
    }
    dag = Pipeline(config)
    # human 节点会抛 SuspendExecution
    with pytest.raises(SuspendExecution):
        await dag.run()

async def test_load_dag_human_with_condition() -> None:
    """human 节点支持 condition 表达式 —— False 时跳过审核。"""
    config = {'nodes': {'data': {'type': 'test_fetch'}, 'review': {'type': 'human', 'depends_on': ['data'], 'condition': '$approved == true'}, '__start__': {'type': 'start', 'inputs': {'approved': {'default': False}}}}}

    dag = Pipeline(config)
    results = await dag.run()
    assert results["review"].status == NodeStatus.SKIPPED

def test_registry_only_lookup() -> None:
    # type 只能引用注册表中的名字，没有 functions 参数可传
    dag = Pipeline({"nodes": {"a": {"type": "test_fetch"}}})
    assert "a" in dag.nodes

    # 点路径不是注册名字，同样被拒绝
    with pytest.raises(ValueError, match="未注册"):
        Pipeline({"nodes": {"b": {"type": "app.registry.other.test_fetch"}}})

def test_parse_retry_forms() -> None:
    mapping = parse_retry({"max_retries": 2, "retry_on": ["RuntimeError"]})
    assert mapping is not None
    assert mapping.max_retries == 2
    assert mapping.retry_on == (RuntimeError,)

    with pytest.raises(ValueError, match="未知异常"):
        parse_retry({"retry_on": ["NoSuchError"]})

def test_validation_errors() -> None:
    with pytest.raises(ValueError, match="不支持的字段"):
        Pipeline({"nodes": {"a": {"kind": "human"}}})  # kind 已废除，只有 type
    with pytest.raises(ValueError, match="不支持的字段"):
        Pipeline({"nodes": {"a": {"type": "test_fetch", "bogus": 1}}})
    with pytest.raises(ValueError, match="需要 'type'"):
        Pipeline({"nodes": {"a": {}}})
    with pytest.raises(ValueError, match="类型函数 'no_such_fn' 未注册"):
        Pipeline({"nodes": {"a": {"type": "no_such_fn"}}})
    with pytest.raises(ValueError, match="不在 DAG 中"):
        Pipeline({"nodes": {"a": {"type": "test_fetch", "depends_on": ["ghost"]}}})
    with pytest.raises(ValueError, match="循环依赖"):
        Pipeline(
            {
                "nodes": {
                    "a": {"type": "test_fetch", "depends_on": ["b"]},
                    "b": {"type": "test_fetch", "depends_on": ["a"]},
                }
            }
        )
    # human 节点不再需要 approver
    dag = Pipeline({"nodes": {"r": {"type": "human"}}})
    assert "r" in dag.nodes
    with pytest.raises((ValueError, TypeError)):
        Pipeline(123)  # type: ignore[arg-type]

def test_load_dag_from_yaml(tmp_path) -> None:
    p = tmp_path / "pipeline.yaml"
    p.write_text(
        "name: tiny\nnodes:\n  fetch:\n    type: test_fetch\n    retry: 2\n",
        encoding="utf-8",
    )
    dag = Pipeline(p)
    assert isinstance(dag, Pipeline)
    assert dag.name == "tiny"
    assert dag.nodes["fetch"].retry == RetryPolicy(max_retries=2)

def test_load_dag_bad_file(tmp_path) -> None:
    with pytest.raises(ValueError, match="无法读取配置文件"):
        Pipeline(tmp_path / "missing.yaml")

    bad = tmp_path / "bad.yaml"
    bad.write_text("nodes: [unclosed", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML 无效"):
        Pipeline(bad)

# ---------------------------------------------------------------------------
# required_inputs — 必填输入声明与校验
# ---------------------------------------------------------------------------

async def test_required_inputs_enforced_at_run(registered: Any) -> None:
    """inputs 声明的必填键：run() 对生效输入强制契约——缺必填开跑前即 ValueError。"""
    ran = {"n": 0}

    async def only(ctx: dict[str, Any]) -> str:
        ran["n"] += 1
        return ctx["query"]

    registered("t_only", only)
    dag = Pipeline({'nodes': {'only': {'type': 't_only'}, '__start__': {'type': 'start', 'inputs': {'query': {'required': True}}}}})

    assert dag.validate() == []

    with pytest.raises(ValueError, match="必填参数缺失"):
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
    dag = Pipeline(
        {'nodes': {'only': {'type': 't_required_default'}, '__start__': {'type': 'start', 'inputs': {'query': {'required': True, 'default': '建议值'}}}}})
    assert dag.required_inputs == ["query"]
    assert dag.default_inputs == {"query": "建议值"}  # 必填键的 default 也进回填视图

    results = await dag.run()  # 不传参 → default 顶班
    assert results["only"].output == "建议值"
    assert ran["n"] == 1

    # 显式空 = 未提供，required=True 拒绝
    with pytest.raises(ValueError, match="必填参数缺失"):
        await dag.run(inputs={})

    results = await dag.run(inputs={"query": "显式值"})
    assert results["only"].output == "显式值"
    assert ran["n"] == 2

def test_required_inputs_bad_type_rejected() -> None:
    """required 布尔值 → 类型校验。"""
    errors = validate_config({'nodes': {'__start__': {'type': 'start', 'inputs': {'query': {'required': 'yes'}}}}})
    assert errors == ["参数 'query': required 必须是布尔值"]

async def test_required_inputs_empty_values_rejected(registered: Any) -> None:
    """必填校验不看 falsy：null/空串/空白串算未提供，0/False 是合法值。"""

    async def only(ctx: dict[str, Any]) -> Any:
        return ctx["count"]

    registered("t_echo_count", only)
    dag = Pipeline(
        {'nodes': {'echo': {'type': 't_echo_count'}, '__start__': {'type': 'start', 'inputs': {'count': {'required': True}}}}})

    # key 存在即算已提供（不检查值内容）
    for val in (None, "", "   ", 0, False):
        results = await dag.run(inputs={"count": val})
        assert results["echo"].output == val

async def test_undeclared_inputs_reject_all_inputs(registered: Any) -> None:
    """输入键必须是声明参数的子集：声明了 inputs → 实际输入 ⊆ 声明键；
    未声明 inputs → 白名单为空，任何输入键都算未声明（loop body
    不受影响——loop_func 直接驱动执行器，不走 run() 的输入契约）。"""

    async def echo(ctx: dict[str, Any]) -> Any:
        return ctx["extra"]

    registered("t_echo_extra", echo)

    # 声明了 inputs 契约：extra 静默忽略（只取声明的 key）
    declared = Pipeline(
        {'nodes': {'echo': {'type': 't_echo_extra'}, '__start__': {'type': 'start', 'inputs': {'q': {'required': True}}}}})
    results = await declared.run(inputs={"q": "ok", "extra": 1})
    assert results["echo"].output == "ok"  # extra 被忽略

    # 未声明 inputs：无 start 节点，原样进 ctx
    free = Pipeline({"nodes": {"echo": {"type": "t_echo_extra"}}})
    results = await free.run(inputs={"extra": 1})
    assert results["echo"].output == 1

    # 未声明 inputs = 自由上下文种子：run() 不设白名单，原样进 ctx
    results = await free.run(inputs={"extra": 1})
    assert results["echo"].output == 1

def test_input_keys_clash_node_names_rejected() -> None:
    """参数键与节点名冲突 → load_dag 拒绝（ctx 命名空间共享）。"""
    with pytest.raises(ValueError, match="参数键与节点名冲突"):
        Pipeline(
            {'nodes': {'only': {'type': 'test_fetch'}, '__start__': {'type': 'start', 'inputs': {'only': {'required': True}}}}})

# ---------------------------------------------------------------------------
# inputs 富声明 — label/description/default/required，与简式归一化
# ---------------------------------------------------------------------------

def test_inputs_rich_form() -> None:
    """inputs 富声明原样进 DAG；默认值/必填键是从声明派生的只读视图
    （前端参数行由展示层从同一声明派生，见 services/pipelines._param_rows）。"""
    params = {
        "query": {"required": True, "label": "查询词", "description": "要检索的内容"},
        "topic": {"default": "默认主题", "label": "主题"},
        "limit": {"default": 5},  # 可选、无 label → 展示层 label 退化为键名
        "body": {"required": True, "type": "paragraph"},  # 多行文本（前端 textarea）
    }
    dag = Pipeline({"nodes": {"only": {"type": "test_fetch"}, "__start__": {"type": "start", "inputs": params}}})
    assert dag.inputs == params  # 声明原样保留
    assert dag.default_inputs == {"topic": "默认主题", "limit": 5}
    assert dag.required_inputs == ["query", "body"]

def test_inputs_multiline_bad_type_rejected() -> None:
    errors = validate_config({'nodes': {'__start__': {'type': 'start', 'inputs': {'b': {'multiline': 'yes'}}}}})
    assert errors == ["参数 'b': multiline 必须是布尔值"]

async def test_inputs_rich_form_runs(registered: Any) -> None:
    """inputs 声明的必填/默认与简式语义一致：run 前校验、默认值进 ctx。"""

    async def search(ctx: dict[str, Any]) -> dict[str, Any]:
        return {"query": ctx["query"], "topic": ctx.get("topic")}

    registered("t_search", search)
    dag = Pipeline(
        {'nodes': {'search': {'type': 't_search'}, '__start__': {'type': 'start', 'inputs': {'query': {'required': True, 'label': '查询词'}, 'topic': {'default': '默认主题'}}}}})
    assert dag.default_inputs == {"topic": "默认主题"}
    assert dag.required_inputs == ["query"]

    with pytest.raises(ValueError, match="必填参数缺失"):
        await dag.run()

    # run(inputs=...) 整体替换默认值；合并语义在 orchestrator（runtime 覆盖默认）
    results = await dag.run(inputs={**dag.default_inputs, "query": "洛伦佐"})
    assert results["search"].output == {"query": "洛伦佐", "topic": "默认主题"}

def test_inputs_unknown_field_rejected() -> None:
    errors = validate_config({'nodes': {'__start__': {'type': 'start', 'inputs': {'q': {'bogus': 1}}}}})
    assert errors == ["参数 'q': 不支持的字段 ['bogus']"]

def test_inputs_collects_all_errors() -> None:
    """多个参数错误一次性全部返回，而非遇错即抛。"""
    errors = validate_config({'nodes': {'__start__': {'type': 'start', 'inputs': {'q': {'bogus': 1}, 'r': {'required': 'yes'}, 's': 'not-a-mapping'}}}})
    assert len(errors) == 3
    assert any("不支持的字段" in e for e in errors)
    assert any("required 必须是布尔值" in e for e in errors)
    assert any("定义必须是映射" in e for e in errors)

def test_inputs_node_name_clash_rejected() -> None:
    """参数键与节点名冲突应被拒绝"""
    assert validate_config(
        {'nodes': {'query': {}, '__start__': {'type': 'start', 'inputs': {'query': {'required': True}}}}}
    ) == ["输入参数键与节点名冲突: query"]

def test_inputs_no_clash_accepted() -> None:
    """参数键与节点名无冲突时通过"""
    assert validate_config(
        {'nodes': {'fetch': {}, '__start__': {'type': 'start', 'inputs': {'query': {'required': True}}}}}
    ) == []

# ---------------------------------------------------------------------------
# review 审核视图 — human 节点声明审核者看什么
# ---------------------------------------------------------------------------

def test_review_declaration_not_validated() -> None:
    """review 卡片声明不校验内容与来源（运行期兜底：字段取不到显示「未提供」）。"""
    # 拼错键 / 参数键 / 上游节点名 / 协议键——载入期一律放行
    assert validate_config({'nodes': {'work': {'type': 'test_fetch'}, 'gate': {'type': 'human', 'depends_on': ['work'], 'inputs': {'_review': {'title': '标题', 'work': '产出', 'ttile': '拼错', 'approve': '协议键'}}}, '__start__': {'type': 'start', 'inputs': {'title': {}}}}}) == []

# ---------------------------------------------------------------------------
# validate_nodes — 节点声明校验
# ---------------------------------------------------------------------------

def test_validate_nodes_accepts() -> None:
    """validate_nodes 接受合法的节点配置"""
    # node 类型节点
    assert validate_config({"nodes": {"a": {"type": "test_fetch"}}}) == []
    assert validate_config({"nodes": {"a": {"type": "test_fetch", "depends_on": []}}}) == []

    # human 类型节点
    assert validate_config({"nodes": {"a": {"type": "human", "prompt": "审核"}}}) == []
    # human 节点 review - 键必须在 inputs 或 nodes 中
    assert validate_config({'nodes': {'a': {'type': 'human', 'prompt': '审核', 'review': {'title': {'label': '标题'}}}, '__start__': {'type': 'start', 'inputs': {'title': {}}}}}) == []

def test_validate_nodes_rejects() -> None:
    """validate_nodes 返回非法配置的全部错误"""
    # nodes 是必填键：未声明（None）与空映射都不行（同一句报错）
    assert validate_config({}) == ["流水线至少需要一个节点"]
    assert validate_config({"nodes": {}}) == ["流水线至少需要一个节点"]

    # nodes 不是 dict（非空非映射才走到类型分支；空列表归入"至少一个节点"）
    assert validate_config({"nodes": ["a"]}) == [
        "nodes 必须是映射(dict)，实际是 list"
    ]

    # 节点定义不是 dict
    assert validate_config({"nodes": {"a": "not_dict"}}) == [
        "节点 'a': 定义必须是映射(dict)，实际是 str"
    ]

    # kind 已废除：报不支持字段 + 缺 type（字段检查先于 membership）
    assert validate_config({"nodes": {"a": {"kind": "quantum"}}}) == [
        "节点 'a': 不支持的字段 ['kind']",
        "节点 'a': 需要 'type'（函数键）",
    ]

    # 不支持的字段
    assert validate_config({"nodes": {"a": {"type": "test_fetch", "bogus": 1}}}) == [
        "节点 'a'（test_fetch）: 不支持的字段 ['bogus']"
    ]

    # node 类型缺少 type
    assert validate_config({"nodes": {"a": {}}}) == [
        "节点 'a': 需要 'type'（函数键）"
    ]

    # type 未注册
    assert validate_config({"nodes": {"a": {"type": "no_such_fn"}}}) == [
        "节点 'a': 类型函数 'no_such_fn' 未注册"
    ]

    # depends_on 类型错误 / 依赖缺失 / 循环依赖（已并入 validate_nodes）
    assert validate_config({"nodes": {"a": {"type": "test_fetch", "depends_on": "fetch"}}}) == [
        "节点 'a': depends_on 必须是字符串列表"
    ]
    assert validate_config(
        {"nodes": {"a": {"type": "test_fetch", "depends_on": ["ghost"]}}}
    ) == ["节点 'a' 依赖的 'ghost' 不在 DAG 中"]
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "depends_on": ["b"]},
        "b": {"type": "test_fetch", "depends_on": ["a"]},
    }}) == ["检测到循环依赖: a → b"]

    # loop 类型缺少 body / condition、condition 引用未知键（condition 校验先于 body 检查）
    assert validate_config({"nodes": {"a": {"type": "loop", "condition": "$x"}}}) == [
        "节点 'a': condition 引用的 'x' 不是参数键或上游依赖节点",
        "循环节点 'a': 需要非空的 'body' 映射",
    ]
    assert validate_config({"nodes": {"a": {"type": "loop", "body": {"b": {"type": "test_fetch"}}}}}) == [
        "循环节点 'a': 需要 'condition' 表达式"
    ]

    # human 节点 review 校验失败（带节点名前缀）
    assert validate_config({'nodes': {'a': {'type': 'human', 'review': {'a': {'label': None}}}, '__start__': {'type': 'start', 'inputs': {'a': {}}}}}) == ["审核节点 'a': review 字段 'a': label 必须是字符串"]

def test_validate_nodes_collects_errors_across_nodes() -> None:
    """不同节点的错误一次性全部返回。"""
    errors = validate_config({
        "nodes": {
            "a": {},                                   # 缺 type
            "b": {"type": "test_fetch", "bogus": 1},    # 不支持的字段
            "c": {"type": "test_fetch"},                # 合法
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
    # 只有 nodes（inputs 可选）
    assert validate_config({"nodes": {"a": {"type": "test_fetch"}}}) == []

    # 完整配置
    assert validate_config({'nodes': {'fetch': {'type': 'test_fetch'}, '__start__': {'type': 'start', 'inputs': {'query': {'required': True}}}}}) == []

def test_validate_config_rejects_bad_structure() -> None:
    """图结构错误：空流水线 / 依赖缺失 / 循环依赖（loop body 递归同查）"""
    # nodes 是必填键：未声明（含只声明 inputs）与空映射都报错
    assert validate_config({}) == ["流水线至少需要一个节点"]
    assert validate_config({'nodes': {'__start__': {'type': 'start', 'inputs': {'q': {'required': True}}}}}) == [
        "流水线至少需要一个节点"
    ]
    assert validate_config({"nodes": {}}) == ["流水线至少需要一个节点"]

    # 依赖缺失
    assert validate_config(
        {"nodes": {"a": {"type": "test_fetch", "depends_on": ["ghost"]}}}
    ) == ["节点 'a' 依赖的 'ghost' 不在 DAG 中"]

    # 循环依赖
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "depends_on": ["b"]},
        "b": {"type": "test_fetch", "depends_on": ["a"]},
    }}) == ["检测到循环依赖: a → b"]

    # loop body 是独立命名空间：body 内依赖缺失带循环节点名前缀
    assert validate_config({"nodes": {
        "l": {
            "type": "loop",
            "condition": "$iteration < 3",
            "body": {"b": {"type": "test_fetch", "depends_on": ["ghost"]}},
        },
    }}) == ["循环节点 'l': 节点 'b' 依赖的 'ghost' 不在 DAG 中"]

def test_validate_config_rejects_param_node_clash() -> None:
    """validate_config 拒绝参数键与节点名冲突"""
    config = {'nodes': {'query': {'type': 'test_fetch'}, '__start__': {'type': 'start', 'inputs': {'query': {'required': True}}}}}

    assert validate_config(config) == ["输入参数键与节点名冲突: query"]

def test_param_node_clash_checked_at_engine() -> None:
    """Pipeline 构造时 __start__ inputs 与节点名冲突由 validate 兜底拦截。"""
    dag = Pipeline({
        "nodes": {
            "__start__": {"type": "start", "inputs": {"query": {"required": True}}},
            "query": {"type": "test_fetch"},
        },
    })
    assert dag.validate() == ["输入参数键与节点名冲突: query"]

def test_validate_config_collects_all_errors() -> None:
    """inputs 与 nodes 的错误一次性全部返回（load_dag 抛出时含全部信息）"""
    config = {'nodes': {'a': {}, 'b': {'type': 'test_fetch', 'bogus': 1}, '__start__': {'type': 'start', 'inputs': {'q': {'bogus': 1}, 'r': {'required': 'yes'}}}}}

    errors = validate_config(config)
    assert len(errors) == 4

    # load_dag 将全部错误合并进同一个 ValueError
    with pytest.raises(ValueError) as exc_info:
        Pipeline(config)
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
    dag = Pipeline(
        {'nodes': {'work': {'type': 't_work'}, 'gate': {'type': 'human', 'depends_on': ['work'], 'description': '重点核对工作成果', 'inputs': {'_review': {'work': {'label': '工作成果'}, 'opt': {'label': '可选参数'}}}}, '__start__': {'type': 'start', 'inputs': {'opt': {}}}}})
    results = await dag.run(approver=approver)
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
    async def approver(node_name: str, payload: dict[str, Any], labels: dict[str, str] | None = None) -> dict[str, Any]:
        return {"approve": True}

    dag = Pipeline(
        {'nodes': {'work': {'type': 'test_fetch'}, 'gate': {'type': 'human', 'depends_on': ['work'], 'inputs': {'_review': {'ttile': '标题'}}}, '__start__': {'type': 'start', 'inputs': {}}}})
    assert dag.human_nodes[0].name == "gate"

def test_review_param_key_allowed() -> None:
    """review 键可以是参数键（含无默认值的可选参数）——校验用参数声明全集。"""
    async def approver(node_name: str, payload: dict[str, Any], labels: dict[str, str] | None = None) -> dict[str, Any]:
        return {"approve": True}

    dag = Pipeline(
        {'nodes': {'gate': {'type': 'human', 'depends_on': [], 'inputs': {'_review': {'q': '查询', 'opt': '可选参数'}}}, '__start__': {'type': 'start', 'inputs': {'q': {'required': True}, 'opt': {}}}}})
    assert dag.human_nodes[0].name == "gate"

# ---------------------------------------------------------------------------
# condition 表达式 — 声明层分流与载入期校验
# ---------------------------------------------------------------------------

async def test_condition_expression_runs_and_skips() -> None:
    """等值表达式按 inputs 值分流：pick == a / pick == b 各自命中。"""
    config = {'nodes': {'a': {'type': 'test_fetch', 'condition': '$pick == a'}, 'b': {'type': 'test_fetch', 'condition': '$pick == b'}, '__start__': {'type': 'start', 'inputs': {'pick': {}}}}}

    results = await Pipeline(config).run(inputs={"pick": "a"})
    assert results["a"].status is NodeStatus.COMPLETED
    assert results["b"].status is NodeStatus.SKIPPED

    results = await Pipeline(config).run(inputs={"pick": "b"})
    assert results["a"].status is NodeStatus.SKIPPED
    assert results["b"].status is NodeStatus.COMPLETED

async def test_condition_expression_on_wired_key() -> None:
    """条件在接线视图上求值：$node.field 点路径取字段后直接比较。"""
    config = {
        "nodes": {
            "data": {"type": "test_fetch"},
            "gold": {
                "type": "test_fetch",
                "depends_on": ["data"],
                "condition": "$tier == gold",
                "inputs": {"tier": "$data.title"},  # title = "DAG Flow v0.1" ≠ gold
            },
        },
    }
    results = await Pipeline(config).run()
    assert results["gold"].status is NodeStatus.SKIPPED

async def test_condition_expression_dollar_reference() -> None:
    """$ 引用前缀：condition 直接引用上游输出字段，无需 inputs 接线中转。"""
    config = {
        "nodes": {
            "data": {"type": "test_fetch"},
            "gold": {
                "type": "test_fetch",
                "depends_on": ["data"],
                "condition": "$data.title == 'DAG Flow v0.1'",
            },
        },
    }
    results = await Pipeline(config).run()
    assert results["gold"].status is NodeStatus.COMPLETED

    config["nodes"]["gold"]["condition"] = "$data.title == nope"
    results = await Pipeline(config).run()
    assert results["gold"].status is NodeStatus.SKIPPED

async def test_condition_boolean_constants() -> None:
    """condition: true/false 布尔常量 —— false 当开关恒跳过，true 恒执行。"""
    config = {
        "nodes": {
            "off": {"type": "test_fetch", "condition": False},
            "on": {"type": "test_fetch", "condition": True},
        },
    }
    assert validate_config(config) == []
    results = await Pipeline(config).run()
    assert results["off"].status is NodeStatus.SKIPPED
    assert results["on"].status is NodeStatus.COMPLETED

    # loop 的 condition: false 合法（body 一轮不跑）；condition: null 等同未声明
    assert validate_config({"nodes": {
        "l": {"type": "loop", "body": {"t": {"type": "test_fetch"}}, "condition": False},
    }}) == []
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "condition": None},
    }}) == []

def test_condition_refs_validated() -> None:
    """condition 引用键做来源校验：未声明依赖/参数的键被拒绝。"""
    # 未声明依赖 → 报错
    assert validate_config({"nodes": {
        "甲": {"type": "test_fetch"},
        "乙": {"type": "test_fetch", "condition": "$甲.title == x"},
    }}) == ["节点 '乙': condition 引用的 '甲' 不是参数键或上游依赖节点"]

    # iteration 是 loop 运行期注入键，放行
    assert validate_config({'nodes': {'甲': {'type': 'test_fetch', 'condition': '$iteration < 3'}, '__start__': {'type': 'start', 'inputs': {}}}}) == []

# ---------------------------------------------------------------------------
# 真实示例流水线 02_condition.yaml — 级联 skip 与 final_answer 汇合
# ---------------------------------------------------------------------------

async def _run_condition_yaml(
    monkeypatch: pytest.MonkeyPatch, prompt: str, classify_raw: str = ""
) -> dict[str, Any]:
    """桩掉 Ollama 跑真实 02_condition.yaml：classify_raw 控制 llm_classify 的
    模型输出（空串 = 预期不触达模型，如「人工」关键词短路）。"""
    from app.services import llm as llm_mod

    async def fake_chat(model: str, messages: list[dict[str, str]], tools: Any = None) -> dict:
        assert classify_raw, "本用例不应触达 LLM"
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "意图分类器" in system:
            content = classify_raw
        elif "知识库问答助手" in system:
            content = "知识库支路答复"
        else:
            content = "闲聊支路答复"
        return {"content": content, "tool_calls": []}

    monkeypatch.setattr(llm_mod, "llm_chat_call", fake_chat)
    config = yaml.safe_load((settings.PIPELINES_DIR / "02_condition.yaml").read_text(encoding="utf-8"))
    dag = Pipeline(config)
    return await dag.run(inputs={"prompt": prompt})

async def test_condition_yaml_chat_branch_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat 支路：rag 支路整条级联跳过，final_answer 汇合取 chat 答复。"""
    results = await _run_condition_yaml(monkeypatch, "你好呀", classify_raw="chat")
    assert results["intent_recognition"].status is NodeStatus.COMPLETED
    assert results["llm_chat"].status is NodeStatus.COMPLETED
    assert results["rag_retrieve"].status is NodeStatus.SKIPPED
    assert results["format_chunks"].status is NodeStatus.UPSTREAM_SKIPPED
    assert results["rag_answer"].status is NodeStatus.UPSTREAM_SKIPPED
    assert results["final_answer"].status is NodeStatus.COMPLETED
    assert results["final_answer"].output == {"branch": "llm_chat", "answer": "闲聊支路答复"}

async def test_condition_yaml_rag_branch_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """rag 支路：chat 支路条件跳过，final_answer 汇合取 rag 答复。"""
    results = await _run_condition_yaml(monkeypatch, "北境要塞是什么", classify_raw="rag")
    assert results["llm_chat"].status is NodeStatus.SKIPPED
    assert results["rag_retrieve"].status is NodeStatus.COMPLETED
    assert results["format_chunks"].status is NodeStatus.COMPLETED
    assert results["rag_answer"].status is NodeStatus.COMPLETED
    assert results["final_answer"].status is NodeStatus.COMPLETED
    assert results["final_answer"].output == {"branch": "rag_answer", "answer": "知识库支路答复"}

async def test_condition_yaml_human_keyword_cascades_to_join(monkeypatch: pytest.MonkeyPatch) -> None:
    """「人工」关键词短路判 human：两条支路全跳过，汇合节点级联跳过。
    （human_service 支路已从示例移除，转人工意图暂无落点——全跳过时
    final_answer 不执行，run 空手完成。）"""
    results = await _run_condition_yaml(monkeypatch, "我要找人工客服")
    assert results["llm_chat"].status is NodeStatus.SKIPPED
    assert results["rag_answer"].status is NodeStatus.UPSTREAM_SKIPPED
    assert results["final_answer"].status is NodeStatus.UPSTREAM_SKIPPED

def test_condition_expression_validation() -> None:
    """表达式的载入期校验：类型、语法、引用键存在性、loop 的 iteration。"""
    # 旧的单键映射形式（函数调用）不再支持
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "condition": {"t_eq": "a"}},
    }}) == [
        "节点 'a': condition 必须是非空表达式字符串"
        "（如 $intent == chat / $merge / not $flag），实际是 {'t_eq': 'a'}"
    ]

    # 语法错误：缺键
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "condition": "== chat"},
    }}) == [
        "节点 'a': 条件表达式 '== chat' 无法解析"
        "（写法如 ``$intent == chat``、``$merge``、``not $flag``）"
    ]

    # 引用键做来源校验：未声明依赖/参数的键被拒绝
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "condition": "$pick2 == a"},
    }}) == ["节点 'a': condition 引用的 'pick2' 不是参数键或上游依赖节点"]
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "condition": "$typo.field == x"},
    }}) == ["节点 'a': condition 引用的 'typo' 不是参数键或上游依赖节点"]
    # iteration 是 loop 运行期注入键，放行
    assert validate_config({"nodes": {
        "a": {"type": "test_fetch", "condition": "$iteration < 3"},
    }}) == []
    # 依赖声明了 data → $data 合法
    assert validate_config({"nodes": {
        "data": {"type": "test_fetch"},
        "gold": {"type": "test_fetch", "depends_on": ["data"], "condition": "$data.title == gold"},
    }}) == []

# ---------------------------------------------------------------------------
# inputs $ 引用来源校验
# ---------------------------------------------------------------------------

def test_input_refs_validated() -> None:
    """inputs 中 $ 引用的根键必须是上游依赖或参数键。"""
    # 合法引用：$data 来自 depends_on
    assert validate_config({"nodes": {
        "data": {"type": "test_fetch"},
        "work": {"type": "test_fetch", "depends_on": ["data"], "inputs": {"body": "$data.title"}},
    }}) == []

    # 合法引用：$query 来自 __start__ 参数
    assert validate_config({'nodes': {
        'work': {'type': 'test_fetch', 'inputs': {'q': '$query'}},
        '__start__': {'type': 'start', 'inputs': {'query': {'required': True}}},
    }}) == []

    # 非法引用：$ghost 既不是参数也不是上游节点
    assert validate_config({"nodes": {
        "work": {"type": "test_fetch", "inputs": {"body": "$ghost.output"}},
    }}) == ["节点 'work': inputs 引用 $ghost，不是参数键或上游依赖节点"]

    # _ 前缀键（如 _review）不校验
    assert validate_config({"nodes": {
        "work": {"type": "test_fetch"},
        "gate": {"type": "human", "depends_on": ["work"], "inputs": {"_review": {"title": "标题"}}},
    }}) == []
