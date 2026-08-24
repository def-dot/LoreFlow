"""
Declarative configuration layer for DAG Flow.
"""

from __future__ import annotations

import yaml
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.registry import REGISTRY, NodeType

from .dag import DAG, find_cycle
from .node import ApproverFunc, Node
from .resolve import parse_retry
from .types import NodeEventFunc


#: Fields each kind accepts; anything else in a node spec raises.
_KIND_FIELDS = {
    "node": {"type", "depends_on", "retry", "timeout", "condition", "metadata"},
    "human": {"depends_on", "retry", "prompt", "condition", "review"},
    "loop": {"depends_on", "retry", "timeout", "condition", "body", "max_iterations"},
}

#: Fields accepted per key in a ``params`` mapping; anything else raises.
_PARAM_FIELDS = {"label", "description", "default", "required", "multiline"}


def validate_params(config: dict[str, Any]) -> list[str]:
    """校验顶层 ``params`` 声明，返回全部错误（空列表 = 合法）。

    每键 spec 只接受 ``{label, description, default, required, multiline}``。
    输入键与节点名不能相同。
    """
    errors: list[str] = []
    params = config.get("params")
    if params is None:
        return errors
    if not isinstance(params, dict):
        return [f"params 必须是映射(dict)，实际是 {type(params).__name__}"]

    nodes = config.get("nodes") or {}
    if nodes:
        clash = sorted(set(params) & set(nodes))
        if clash:
            errors.append(f"输入参数键与节点名冲突: {', '.join(clash)}")

    for name, spec in params.items():
        if not isinstance(spec, dict):
            errors.append(f"参数 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")
            continue
        unknown = set(spec) - _PARAM_FIELDS
        if unknown:
            errors.append(f"参数 {name!r}: 不支持的字段 {sorted(unknown)}")
        for text_field in ("label", "description"):
            if spec.get(text_field) is not None and not isinstance(spec[text_field], str):
                errors.append(f"参数 {name!r}: {text_field} 必须是字符串")
        required_value = spec.get("required")
        if required_value is not None and not isinstance(required_value, bool):
            errors.append(f"参数 {name!r}: required 必须是布尔值")
        multiline = spec.get("multiline", False)
        if not isinstance(multiline, bool):
            errors.append(f"参数 {name!r}: multiline 必须是布尔值")
    return errors


def _validate_review(
    review_spec: dict[str, Any], available_keys: set[str]
) -> list[str]:
    """校验 human 节点的 review 声明并检查键引用，返回全部错误（空列表 = 合法）。
    """
    if not isinstance(review_spec, dict):
        return [f"review 必须是映射，实际是 {type(review_spec).__name__}"]

    if not review_spec:
        return ["review 声明不能为空映射"]

    errors: list[str] = []
    unknown_keys = [k for k in review_spec if k not in available_keys]
    if unknown_keys:
        errors.append(f"review 引用了未声明的键 {', '.join(unknown_keys)}")

    for key, val in review_spec.items():
        if not isinstance(val, dict):
            errors.append(
                f"review 字段 {key!r}: 必须是 {{label: 文本}} 格式，实际是 {type(val).__name__}"
            )
            continue

        unknown = set(val) - {"label"}
        if unknown:
            errors.append(f"review 字段 {key!r}: 不支持的字段 {sorted(unknown)}")

        label = val.get("label")
        if not isinstance(label, str):
            errors.append(f"review 字段 {key!r}: label 必须是字符串")
    return errors


def validate_nodes(config: dict[str, Any]) -> list[str]:
    """校验顶层 nodes 声明，返回全部错误（空列表 = 合法）。

    含依赖存在性与环检测；loop 的 body 是独立命名空间，递归走同一入口
    （不传 params，与构建期嵌套 load_dag 的语义一致）。
    """
    nodes = config.get("nodes")
    if not nodes:
        return ["流水线至少需要一个节点"]
    if not isinstance(nodes, dict):
        return [f"nodes 必须是映射(dict)，实际是 {type(nodes).__name__}"]

    errors: list[str] = []
    params = config.get("params")
    # 可用键集合 = 参数键 + 节点名（params 类型错误已在 validate_params 报告）
    available = set(nodes) | (set(params) if isinstance(params, dict) else set())

    edges: dict[str, list[str]] = {}
    deps_missing = False
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

        # 依赖存在性：depends_on 引用的键和 condition/type 一样是逐节点引用检查
        deps = spec.get("depends_on")
        if deps is None:
            deps = []
        elif not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errors.append(f"节点 {name!r}: depends_on 必须是字符串列表")
            deps = []
        edges[name] = deps
        for dep in deps:
            if dep not in nodes:
                errors.append(f"节点 {name!r} 依赖的 {dep!r} 不在 DAG 中")
                deps_missing = True

        # 校验 condition 字段（如果存在）
        condition_key = spec.get("condition")
        if condition_key is not None and condition_key not in REGISTRY:
            errors.append(f"节点 {name!r}（{kind}）: 条件函数 {condition_key!r} 未注册")

        # kind 特定必需字段校验
        if kind == "node":
            type_key = spec.get("type")
            if not type_key:
                errors.append(f"节点 {name!r}: 需要 'type'（函数键）")
            elif type_key not in REGISTRY:
                errors.append(f"节点 {name!r}: 类型函数 {type_key!r} 未注册")
        elif kind == "human":
            review_spec = spec.get("review")
            if review_spec is not None:
                errors.extend(
                    f"审核节点 {name!r}: {msg}" for msg in _validate_review(review_spec, available)
                )
        elif kind == "loop":
            body = spec.get("body")
            if not isinstance(body, dict) or not body:
                errors.append(f"循环节点 {name!r}: 需要非空的 'body' 映射")
            if not condition_key:
                errors.append(f"循环节点 {name!r}: 需要 'condition' 函数键")
            elif isinstance(body, dict) and body:
                errors.extend(
                    f"循环节点 {name!r}: {msg}"
                    for msg in validate_nodes({"nodes": body})
                )

    # 环检测只在依赖齐全时做（与 DAG.validate 同约定：缺失依赖时环结论不可信）
    if not deps_missing:
        cycle = find_cycle(edges)
        if cycle:
            errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
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
    - params 声明（字段合法性、与节点名无冲突）
    - nodes 声明（必须声明且非空、字段合法性、引用校验、依赖存在性、环；
      loop body 递归同查）
    - review 声明格式和键引用（必须在参数键或节点名中）
    """
    return validate_params(config) + validate_nodes(config)


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
        params=config.get("params") or {},
        on_event=on_event,
    )

    # validate_config 已保证 nodes 必声明且非空，直接索引
    for name, spec in config["nodes"].items():
        kind = spec.get("kind", "node")

        deps = spec.get("depends_on") or []
        retry = parse_retry(spec.get("retry"))

        if kind == "human":
            condition = spec.get("condition")
            cond_func = None
            if condition:
                cond_func = REGISTRY[condition].func
            dag.human_node(
                name,
                depends_on=deps,
                prompt=spec.get("prompt"),
                condition=cond_func,
                retry=retry,
                approver=approver,
                review=spec.get("review"),
            )

        elif kind == "loop":
            body = spec.get("body")

            # Body nodes go through the same parsing path as top-level nodes.
            body_dag = load_dag({"nodes": body}, approver=approver)
            cond_type = REGISTRY[spec["condition"]]
            dag.loop_node(
                name,
                body_nodes=list(body_dag.nodes.values()),
                condition=cond_type.func,
                depends_on=deps,
                max_iterations=int(spec.get("max_iterations", 100)),
                retry=retry,
                timeout=spec.get("timeout"),
            )

        else:
            condition = spec.get("condition")
            cond_func = None
            if condition:
                cond_func = REGISTRY[condition].func

            node_type = REGISTRY[spec["type"]]
            dag.add_node(
                Node(
                    name=name,
                    func=node_type.func,
                    depends_on=deps,
                    retry=retry,
                    timeout=spec.get("timeout"),
                    condition=cond_func,
                    metadata={
                        "type": node_type.name,
                        "label": node_type.label,
                        **(spec.get("metadata") or {}),
                    },
                )
            )
    return dag
