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


def validate_params(params: dict[str, Any] | None = None, nodes: dict[str, Any] | None = None) -> None:
    """校验顶层 ``params`` 声明；
    每键 spec 只接受 ``{label, description, default, required, multiline}``。
    输入键与节点名不能相同。
    """
    if params is None:
        return
    if not isinstance(params, dict):
        raise ValueError(f"params 必须是映射(dict)，实际是 {type(params).__name__}")

    if nodes:
        param_keys = set(params.keys())
        node_names = set(nodes.keys())
        clash = sorted(param_keys & node_names)
        if clash:
            raise ValueError(f"输入参数键与节点名冲突: {', '.join(clash)}")

    for name, spec in params.items():
        if not isinstance(spec, dict):
            raise ValueError(f"参数 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")
        unknown = set(spec) - _PARAM_FIELDS
        if unknown:
            raise ValueError(f"参数 {name!r}: 不支持的字段 {sorted(unknown)}")
        for text_field in ("label", "description"):
            if spec.get(text_field) is not None and not isinstance(spec[text_field], str):
                raise ValueError(f"参数 {name!r}: {text_field} 必须是字符串")
        required_value = spec.get("required")
        if required_value is not None and not isinstance(required_value, bool):
            raise ValueError(f"参数 {name!r}: required 必须是布尔值")
        multiline = spec.get("multiline", False)
        if not isinstance(multiline, bool):
            raise ValueError(f"参数 {name!r}: multiline 必须是布尔值")


def _validate_review(
    review_spec: dict[str, Any], available_keys: set[str]
) -> None:
    """校验 human 节点的 review 声明并检查键引用。

    校验项：
    - 键引用：review 键必须在 available_keys 中（参数键或节点名）
    - 格式：{key: {label: 文本}}，label 必须为字符串
    """
    if not isinstance(review_spec, dict):
        raise ValueError(f"review 必须是映射，实际是 {type(review_spec).__name__}")

    if not review_spec:
        raise ValueError("review 声明不能为空映射")

    unknown_keys = [k for k in review_spec if k not in available_keys]
    if unknown_keys:
        raise ValueError(f"review 引用了未声明的键 {', '.join(unknown_keys)}")

    for key, val in review_spec.items():
        if not isinstance(val, dict):
            raise ValueError(
                f"review 字段 {key!r}: 必须是 {{label: 文本}} 格式，实际是 {type(val).__name__}"
            )

        unknown = set(val) - {"label"}
        if unknown:
            raise ValueError(f"review 字段 {key!r}: 不支持的字段 {sorted(unknown)}")

        label = val.get("label")
        if not isinstance(label, str):
            raise ValueError(f"review 字段 {key!r}: label 必须是字符串")


def validate_nodes(nodes: dict[str, Any]) -> None:
    """校验顶层 nodes 声明；

    每节点校验：
    - 定义必须是 dict
    - kind 必须是 node|human|loop
    - 字段必须在允许列表内（_KIND_FIELDS）
    - kind 特定必需字段：
      * human: prompt（必需），review（可选，需符合富映射格式）
      * loop: body（必需非空）、condition（必需）
      * node: type（必需）
    """
    if not isinstance(nodes, dict):
        raise ValueError(f"nodes 必须是映射(dict)，实际是 {type(nodes).__name__}")

    for name, spec in nodes.items():
        if not isinstance(spec, dict):
            raise ValueError(f"节点 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")

        kind = spec.get("kind", "node")
        allowed = _KIND_FIELDS.get(kind)
        if allowed is None:
            raise ValueError(f"节点 {name!r}: 未知类型 {kind!r}（支持 node|human|loop）")

        unknown = set(spec) - {"kind"} - allowed
        if unknown:
            raise ValueError(f"节点 {name!r}（{kind}）: 不支持的字段 {sorted(unknown)}")

        # kind 特定必需字段校验
        if kind == "node":
            if "type" not in spec:
                raise ValueError(f"节点 {name!r}: 需要 'type'（函数键）")
        elif kind == "human":
            # review 校验移到 validate_config 中，因为需要知道可用键（参数+节点名）
            pass
        elif kind == "loop":
            body = spec.get("body")
            if not isinstance(body, dict) or not body:
                raise ValueError(f"循环节点 {name!r}: 需要非空的 'body' 映射")
            if not spec.get("condition"):
                raise ValueError(f"循环节点 {name!r}: 需要 'condition' 函数键")


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


def validate_config(config: dict[str, Any]) -> None:
    """校验完整的 DAG 配置；只做校验，不解析/构建。

    校验项：
    - params 声明（字段合法性、与节点名无冲突）
    - nodes 声明（结构合法性、kind 特定必需字段）
    - review 声明格式和键引用（必须在参数键或节点名中）
    """
    params = config.get("params")
    nodes = config.get("nodes") or {}

    validate_params(params, nodes)
    validate_nodes(nodes)

    # 可用键集合 = 参数键 + 节点名
    available = set(nodes.keys()) | set(params.keys() if params else [])

    # 校验 human 节点的 review 声明（格式 + 键引用）
    for name, spec in nodes.items():
        kind = spec.get("kind", "node")
        if kind == "human":
            review_spec = spec.get("review")
            if review_spec is not None:
                _validate_review(review_spec, available)


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

    validate_config(config)

    # human 节点的 review 声明（载入后统一校验键的存在性）
    review_specs: dict[str, dict[str, str]] = {}

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
            # review 富声明：校验后派生 {key: label} 视图（空 label 退化为键名）
            review_spec = spec.get("review")
            review: dict[str, str] | None = None
            if review_spec is not None:
                review = {key: val["label"] or key for key, val in review_spec.items()}
                review_specs[name] = review
            cond_func = None
            if condition:
                cond_type = REGISTRY.get(condition)
                if cond_type is None:
                    raise ValueError(f"条件谓词 {condition!r} 未注册")
                cond_func = cond_type.func
            dag.human_node(
                name,
                depends_on=deps,
                prompt=spec.get("prompt"),
                condition=cond_func,
                retry=retry,
                approver=approver,
                review=review,
            )

        elif kind == "loop":
            body = spec.get("body")
            if not isinstance(body, dict) or not body:
                raise ValueError(f"循环节点 {name!r} 需要非空的 'body' 映射")
            
            if not spec.get("condition"):
                raise ValueError(f"循环节点 {name!r} 需要 'condition' 函数键")

            # Body nodes go through the same parsing path as top-level nodes.
            body_dag = load_dag({"nodes": body}, approver=approver)
            cond_type = REGISTRY.get(spec["condition"])
            if cond_type is None:
                raise ValueError(f"条件谓词 {spec['condition']!r} 未注册")
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
            if "type" not in spec:
                raise ValueError(f"节点 {name!r} 需要 'type'（函数键）")
            condition = spec.get("condition")
            # 注册表类型键与 label 写进 metadata 供 to_mermaid 展示；YAML 的
            # metadata 在后，同名 key（如自定义 label）可覆盖默认值
            node_type = REGISTRY.get(spec["type"])
            if node_type is None:
                raise ValueError(f"节点 {spec['type']!r} 未注册")
            cond_func = None
            if condition:
                cond_type = REGISTRY.get(condition)
                if cond_type is None:
                    raise ValueError(f"条件谓词 {condition!r} 未注册")
                cond_func = cond_type.func
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
