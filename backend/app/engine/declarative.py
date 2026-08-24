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


def validate_params(config: dict[str, Any]) -> None:
    """校验顶层 ``params`` 声明；只做校验，不解析/派生数据。

    每键 spec 只接受 ``{label, description, default, required, multiline}``。
    required 与 default 可共存：必填键的 default 只是表单建议值，不参与
    回填（见 ``DAG.default_inputs``）。声明原样进 ``DAG.params``，必填/
    默认值视图与前端参数行各自按需派生。
    """
    params = config.get("params")
    if params is None:
        return
    if not isinstance(params, dict):
        raise ValueError(f"params 必须是映射(dict)，实际是 {type(params).__name__}")

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


def validate_review(spec: dict[str, Any]) -> None:
    """校验 human 节点的 review 视图声明，必须为富映射格式。

    仅支持形式：``{key: {label: 显示文本}}``，其中 label 必须为字符串（允许空字符串）。
    校验通过无返回，校验失败抛出 ValueError。
    """
    if not isinstance(spec, dict):
        raise ValueError(f"review 必须是映射，实际是 {type(spec).__name__}")

    if not spec:
        raise ValueError("review 声明不能为空映射")

    for key, val in spec.items():
        if not isinstance(val, dict):
            raise ValueError(f"review 字段 {key!r}: 必须是 {{label: 文本}} 格式，实际是 {type(val).__name__}")

        unknown = set(val) - {"label"}
        if unknown:
            raise ValueError(f"review 字段 {key!r}: 不支持的字段 {sorted(unknown)}")

        label = val.get("label")
        if not isinstance(label, str):
            raise ValueError(f"review 字段 {key!r}: label 必须是字符串")


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

    validate_params(config)
    # human 节点的 review 声明（载入后统一校验键的存在性）
    review_specs: dict[str, dict[str, str]] = {}

    dag = DAG(
        config.get("name", "dag"),
        params=config.get("params") or {},
        on_event=on_event,
    )

    for name, spec in (config.get("nodes") or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(f"节点 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")

        kind = spec.get("kind", "node")
        allowed = _KIND_FIELDS.get(kind)
        if allowed is None:
            raise ValueError(f"节点 {name!r}: 未知类型 {kind!r}（支持 node|human|loop）")
        
        unknown = set(spec) - {"kind"} - allowed
        if unknown:
            raise ValueError(f"节点 {name!r}（{kind}）: 不支持的字段 {sorted(unknown)}")

        deps = spec.get("depends_on") or []
        retry = parse_retry(spec.get("retry"))

        if kind == "human":
            condition = spec.get("condition")
            # review 富声明：校验后派生 {key: label} 视图（空 label 退化为键名）
            review_spec = spec.get("review")
            review: dict[str, str] | None = None
            if review_spec is not None:
                validate_review(review_spec)
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

    # 输入键与节点名共享 ctx 命名空间：声明的参数键重名会被节点输出覆盖，直接拒绝
    clash = sorted(set(dag.params) & set(dag.node_names))
    if clash:
        raise ValueError(f"输入参数键与节点名冲突: {', '.join(clash)}")

    # review 视图键必须在运行时可能出现的名字里（参数键或节点名）：
    # 拼写错误在载入时就拦截，而不是审核时静默显示 None
    available = set(dag.node_names) | set(dag.params)
    for node_name, view in review_specs.items():
        unknown = [k for k in view if k not in available]
        if unknown:
            raise ValueError(f"审核节点 {node_name!r}: review 引用了未声明的键 {', '.join(unknown)}")
    return dag
