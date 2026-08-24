"""
Declarative configuration layer for DAG Flow.
"""

from __future__ import annotations

import yaml
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.registry import REGISTRY, NodeType

from .dag import DAG
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

    校验项：
    - 键引用：review 键必须在 available_keys 中（参数键或节点名）
    - 格式：{key: {label: 文本}}，label 必须为字符串
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

    每节点校验：
    - 定义必须是 dict
    - kind 必须是 node|human|loop
    - 字段必须在允许列表内（_KIND_FIELDS）
    - condition 字段（如果存在）必须在 REGISTRY 中
    - kind 特定必需字段：
      * human: review（可选，需符合富映射格式且键已声明）
      * loop: body（必需非空）、condition（必需且已注册）
      * node: type（必需且已注册）
    """
    errors: list[str] = []
    nodes = config.get("nodes")
    if nodes is None:
        return errors
    if not isinstance(nodes, dict):
        return [f"nodes 必须是映射(dict)，实际是 {type(nodes).__name__}"]

    params = config.get("params")
    # 可用键集合 = 参数键 + 节点名（params 类型错误已在 validate_params 报告）
    available = set(nodes) | (set(params) if isinstance(params, dict) else set())

    for name, spec in nodes.items():
        if not isinstance(spec, dict):
            errors.append(f"节点 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")
            continue

        kind = spec.get("kind", "node")
        allowed = _KIND_FIELDS.get(kind)
        if allowed is None:
            errors.append(f"节点 {name!r}: 未知类型 {kind!r}（支持 node|human|loop）")
            continue

        unknown = set(spec) - {"kind"} - allowed
        if unknown:
            errors.append(f"节点 {name!r}（{kind}）: 不支持的字段 {sorted(unknown)}")

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
    - nodes 声明（结构合法性、kind 特定必需字段、condition/type 注册校验）
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

    for name, spec in (config.get("nodes") or {}).items():
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

            # 注册表类型键与 label 写进 metadata 供 to_mermaid 展示；YAML 的
            # metadata 在后，同名 key（如自定义 label）可覆盖默认值
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

    errors = dag.validate()
    if errors:
        raise ValueError("DAG 配置无效:\n  " + "\n  ".join(errors))

    return dag
