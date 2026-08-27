"""
Declarative configuration layer for DAG Flow.
"""

from __future__ import annotations

import yaml
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.registry import REGISTRY

from . import validate
from .condition import compile_condition, lookup
from .dag import DAG
from .node import ApproverFunc, ConditionFunc, Node
from .resolve import parse_retry
from .types import NodeEventFunc


#: Fields each kind accepts; anything else in a node spec raises.
_KIND_FIELDS = {
    "node": {"type", "label", "depends_on", "inputs", "retry", "timeout", "condition", "metadata"},
    "human": {"label", "depends_on", "inputs", "retry", "prompt", "condition", "review"},
    "loop": {"label", "depends_on", "retry", "timeout", "condition", "body", "max_iterations"},
}


def _wired_view(ctx: dict[str, Any], wiring: dict[str, Any]) -> dict[str, Any]:
    """接线 → 节点视图 ``{**ctx, **{本地键: 视图值}}``。

    值的语义："$" 前缀字符串 = 引用，去前缀按键（可带 ``.field`` 点
    路径下钻字段）取 ctx 值（上游输出/参数；缺失或被条件跳过时为
    None，由节点守卫转中文报错、条件按 False）；其余 = 字面量原样
    注入。视图不写回共享 ctx —— 各节点同名本地键互不串扰。
    """
    def resolve(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("$"):
            return lookup(ctx, v[1:])
        return v

    return {**ctx, **{k: resolve(v) for k, v in wiring.items()}}


def _condition_func(expr: str, wiring: dict[str, Any] | None = None) -> ConditionFunc:
    """条件表达式（+ 可选接线）→ 可调用谓词（语法见 app.engine.condition）。

    wiring 非空时在数据流接线的节点视图上求值 —— 引用键缺失/被条件
    跳过时取到 None，比较类表达式自然为 False。
    """
    cond = compile_condition(expr)

    if not wiring:
        return cond

    def wired(ctx: dict[str, Any]) -> bool:
        return cond(_wired_view(ctx, wiring))

    return wired


def _wired_func(func: Callable[..., Any], wiring: dict[str, Any]) -> Callable[..., Any]:
    """数据流接线视图套在节点函数外（视图语义见 _wired_view；工厂按参捕获，
    循环内多次调用无闭包晚绑定）。"""
    async def wired(ctx: dict[str, Any]) -> Any:
        return await func(_wired_view(ctx, wiring))
    return wired


def _ancestors(name: str, edges: dict[str, Any]) -> set[str]:
    """depends_on 闭包（直接上游及其全部祖先）。"""
    seen: set[str] = set()
    stack = [name]
    while stack:
        for dep in edges.get(stack.pop()) or []:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def validate_nodes(config: dict[str, Any]) -> list[str]:
    """校验顶层 nodes 声明，返回全部错误（空列表 = 合法）。
    """
    nodes = config.get("nodes")
    if not nodes:
        return ["流水线至少需要一个节点"]
    if not isinstance(nodes, dict):
        return [f"nodes 必须是映射(dict)，实际是 {type(nodes).__name__}"]

    errors: list[str] = []
    params = config.get("inputs")
    
    available_ref = set(nodes) | (set(params) if isinstance(params, dict) else set())

    edges: dict[str, Any] = {}
    wirings: dict[str, dict[str, Any]] = {}  # 校验通过的接线，供上游链检查复用
    for name, spec in nodes.items():
        if not isinstance(spec, dict):
            errors.append(f"节点 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")
            edges[name] = []
            continue

        kind = spec.get("kind", "node")
        allowed = _KIND_FIELDS.get(kind)
        if allowed is None:
            errors.append(f"节点 {name!r}: 未知类型 {kind!r}（支持 node|human|loop）")
            edges[name] = []
            continue

        unknown = set(spec) - {"kind"} - allowed
        if unknown:
            errors.append(f"节点 {name!r}（{kind}）: 不支持的字段 {sorted(unknown)}")

        edges[name] = spec.get("depends_on")

        # 数据流接线（node/human）：{本地键: 来源键或字面量}。先于 condition
        # 校验 —— 条件可引用的键 = params ∪ 节点名 ∪ 本节点 inputs 本地键
        wiring = spec.get("inputs") if kind in ("node", "human") else None
        if wiring:
            if not isinstance(wiring, dict):
                errors.append(f"节点 {name!r}: inputs 必须是「本地键: 来源键或字面量」的映射")
                wiring = {}
            for local, source in wiring.items():
                if not (isinstance(source, str) and source.startswith("$")):
                    continue  # 字面量不校验来源
                root = source[1:].partition(".")[0]  # $node.field → node
                if root not in available_ref:
                    errors.append(f"节点 {name!r}: inputs.{local} 引用的 {root!r} 不是节点名或参数键")

        if wiring:
            wirings[name] = wiring

        condition_key = spec.get("condition")
        if condition_key is not None:
            cond_available = available_ref | set(wiring or {})
            if kind == "loop":
                cond_available |= {"iteration"}  # 循环谓词视图每轮注入 iteration
            errors.extend(
                validate.validate_condition(condition_key, name, kind, cond_available)
            )

        # kind 特定必需字段校验
        if kind == "node":
            type_key = spec.get("type")
            if not type_key:
                errors.append(f"节点 {name!r}: 需要 'type'（函数键）")
            elif type_key not in REGISTRY:
                errors.append(f"节点 {name!r}（node）: 类型函数 {type_key!r} 未注册")
        elif kind == "human":
            review_spec = spec.get("review")
            if review_spec is not None:
                errors.extend(
                    f"审核节点 {name!r}: {msg}" for msg in validate.validate_review(review_spec, available_ref)
                )
        elif kind == "loop":
            body = spec.get("body")
            if not isinstance(body, dict) or not body:
                errors.append(f"循环节点 {name!r}: 需要非空的 'body' 映射")
            if not condition_key:
                errors.append(f"循环节点 {name!r}: 需要 'condition' 表达式")
            elif isinstance(body, dict) and body:
                errors.extend(
                    f"循环节点 {name!r}: {msg}"
                    for msg in validate_nodes({"nodes": body})
                )

    errors.extend(validate.validate_graph(edges))

    # 接线来源若是节点，其根键必须在 depends_on 上游链中 —— 否则执行到
    # 本节点时该来源可能尚未运行（数据只能来自已声明的上游或参数）
    for name, wiring in wirings.items():
        ancestors = _ancestors(name, edges)
        for local, source in wiring.items():
            if not (isinstance(source, str) and source.startswith("$")):
                continue  # 字面量不校验来源
            root = source[1:].partition(".")[0]  # $node.field → node
            if root in nodes and root not in ancestors:
                errors.append(f"节点 {name!r}: inputs.{local} 引用的 {root!r} 不在 depends_on 上游链中")
    return errors


def read_yaml(path: str | Path) -> tuple[str, Any]:
    """Read a YAML config file, returning ``(raw_text, config)`` (requires PyYAML).

    ``config`` is always a mapping: empty YAML becomes ``{}``, and a
    non-mapping top level raises ``ValueError``, as do read/parse errors.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取配置文件 {path!r}: {exc}") from exc
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"配置文件 {path!r} 的 YAML 无效: {exc}") from exc
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("顶层必须是映射(dict)")
    return raw, config


def validate_config(config: dict[str, Any]) -> list[str]:
    """校验完整的 DAG 配置，返回全部错误（空列表 = 合法）；不解析/构建。

    收集所有错误而非遇错即抛：人工编辑 YAML 时一次看到全部问题，
    避免"修复 → 重跑 → 发现下一个错"的多轮循环。

    校验项：
    - inputs 声明（字段合法性、与节点名无冲突）
    - nodes 声明（必须声明且非空、字段合法性、引用校验、依赖存在性、环；
      loop body 递归同查）
    - review 声明格式和键引用（必须在 inputs 键或节点名中）
    """
    return (
        validate.validate_params(config.get("inputs"), config.get("nodes") or {})
        + validate_nodes(config)
    )


def load_dag(
    source: str | Path | dict[str, Any],
    approver: ApproverFunc | None = None,
    on_event: NodeEventFunc | None = None,
) -> DAG:
    """Build a :class:`DAG` from a config dict or a YAML/JSON file path.
    """
    if isinstance(source, dict):
        config = source
    elif isinstance(source, (str, Path)):
        _, config = read_yaml(source)
    else:
        raise ValueError(f"配置必须是 dict 或文件路径，实际是 {type(source).__name__}")

    errors = validate_config(config)
    if errors:
        raise ValueError("DAG 配置无效:\n  " + "\n  ".join(errors))

    dag = DAG(
        config.get("name", "dag"),
        # YAML 顶层 ``inputs``（调用方表单声明）→ 引擎内统一叫 params（声明）
        params=config.get("inputs") or {},
        on_event=on_event,
    )

    for name, spec in config["nodes"].items():
        kind = spec.get("kind", "node")

        deps = spec.get("depends_on") or []
        retry = parse_retry(spec.get("retry"))

        if kind == "human":
            condition = spec.get("condition")
            cond_func = _condition_func(condition, spec.get("inputs")) if condition else None
            human = dag.human_node(
                name,
                depends_on=deps,
                prompt=spec.get("prompt"),
                condition=cond_func,
                retry=retry,
                approver=approver,
                review=spec.get("review"),
            )
            if spec.get("label"):
                human.metadata["label"] = spec["label"]

        elif kind == "loop":
            body = spec.get("body")

            # Body nodes go through the same parsing path as top-level nodes.
            body_dag = load_dag({"nodes": body}, approver=approver)
            loop = dag.loop_node(
                name,
                body_nodes=list(body_dag.nodes.values()),
                condition=compile_condition(spec["condition"]),
                depends_on=deps,
                max_iterations=int(spec.get("max_iterations", 100)),
                retry=retry,
                timeout=spec.get("timeout"),
            )
            if spec.get("label"):
                loop.metadata["label"] = spec["label"]

        else:
            condition = spec.get("condition")
            wiring = spec.get("inputs")
            cond_func = _condition_func(condition, wiring) if condition else None

            node_type = REGISTRY[spec["type"]]
            func = node_type.func
            if wiring:
                func = _wired_func(func, wiring)
            dag.add_node(
                Node(
                    name=name,
                    func=func,
                    depends_on=deps,
                    retry=retry,
                    timeout=spec.get("timeout"),
                    condition=cond_func,
                    metadata={
                        "type": node_type.name,
                        # YAML label（中文展示名）优先，缺省用注册表 label
                        "label": spec.get("label") or node_type.label,
                        **(spec.get("metadata") or {}),
                    },
                )
            )
    return dag
