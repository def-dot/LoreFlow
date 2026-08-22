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


def _resolve_type(name: str) -> NodeType:
    """按名字查全局注册表；未注册时抛中文 ValueError（异常处理器映射为 400）。"""
    node_type = REGISTRY.get(name)
    if node_type is None:
        raise ValueError(f"节点 {name!r} 未注册")
    return node_type


def _resolve_function(name: str) -> Callable[..., Any]:
    """按名字解析注册表中的函数本体（条件谓词等只需要 func 时用）。"""
    return _resolve_type(name).func

#: Fields each kind accepts; anything else in a node spec raises.
_KIND_FIELDS = {
    "node": {"type", "depends_on", "retry", "timeout", "condition", "metadata"},
    "human": {"depends_on", "retry", "prompt", "condition"},
    "loop": {"depends_on", "retry", "timeout", "condition", "body", "max_iterations"},
}

#: Fields accepted per key in a ``params`` mapping; anything else raises.
_PARAM_FIELDS = {"label", "description", "default", "required"}


def parse_params(config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """归一化输入参数声明 → ``(参数行, default_inputs, required_inputs)``。

    两种声明形式产出统一的参数行 ``{name, label, description, default,
    has_default, required}``（供 API/前端表单渲染）：

    - 富形式：顶层 ``params``，每键 spec ``{label, description, default,
      required}``，引擎用的默认值/必填键由 spec 派生
    - 简式：``inputs``（默认值映射）+ ``required_inputs``（裸键列表），
      行内 label 退化为键名、无说明

    两种形式互斥（同键两处声明必然二义）。
    """
    params = config.get("params")
    if params is not None and ("inputs" in config or "required_inputs" in config):
        raise ValueError("params 不能与 inputs/required_inputs 混用，统一用 params 声明")

    if params is None:
        required = config.get("required_inputs") or []
        if not isinstance(required, list) or not all(isinstance(k, str) and k for k in required):
            raise ValueError(f"required_inputs 必须是非空字符串列表，实际是 {required!r}")
        defaults = config.get("inputs") or {}
        overlapped = sorted(set(required) & set(defaults))
        if overlapped:
            raise ValueError(f"required_inputs 与 inputs 默认值重叠（必填键不应有默认值）: {', '.join(overlapped)}")
        rows = [{"name": k, "label": k, "description": None, "default": None, "has_default": False, "required": True} for k in required]
        rows += [{"name": k, "label": k, "description": None, "default": v, "has_default": True, "required": False} for k, v in defaults.items()]
        return rows, defaults, list(required)

    if not isinstance(params, dict) or not params:
        raise ValueError(f"params 必须是非空映射(dict)，实际是 {params!r}")
    rows: list[dict[str, Any]] = []
    defaults: dict[str, Any] = {}
    required: list[str] = []
    for name, spec in params.items():
        if not isinstance(spec, dict):
            raise ValueError(f"参数 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")
        unknown = set(spec) - _PARAM_FIELDS
        if unknown:
            raise ValueError(f"参数 {name!r}: 不支持的字段 {sorted(unknown)}")
        for text_field in ("label", "description"):
            if spec.get(text_field) is not None and not isinstance(spec[text_field], str):
                raise ValueError(f"参数 {name!r}: {text_field} 必须是字符串")
        is_required = bool(spec.get("required"))
        has_default = "default" in spec
        if is_required and has_default:
            raise ValueError(f"参数 {name!r}: 必填参数不应有默认值")
        if is_required:
            required.append(name)
        elif has_default:
            defaults[name] = spec["default"]
        rows.append({
            "name": name,
            "label": spec.get("label") or name,
            "description": spec.get("description"),
            "default": spec.get("default"),
            "has_default": has_default,
            "required": is_required,
        })
    return rows, defaults, required


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

    _, defaults, required = parse_params(config)

    dag = DAG(
        config.get("name", "dag"),
        default_inputs=defaults,
        required_inputs=required,
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
            dag.human_node(
                name,
                depends_on=deps,
                prompt=spec.get("prompt"),
                condition=_resolve_function(condition) if condition else None,
                retry=retry,
                approver=approver,
            )

        elif kind == "loop":
            body = spec.get("body")
            if not isinstance(body, dict) or not body:
                raise ValueError(f"循环节点 {name!r} 需要非空的 'body' 映射")
            
            if not spec.get("condition"):
                raise ValueError(f"循环节点 {name!r} 需要 'condition' 函数键")

            # Body nodes go through the same parsing path as top-level nodes.
            body_dag = load_dag({"nodes": body}, approver=approver)
            dag.loop_node(
                name,
                body_nodes=list(body_dag.nodes.values()),
                condition=_resolve_function(spec["condition"]),
                depends_on=deps,
                max_iterations=int(spec.get("max_iterations", 100)),
                retry=retry,
                timeout=spec.get("timeout"),
            )

        else:
            if "type" not in spec:
                raise ValueError(f"节点 {name!r} 需要 'type'（函数键）")
            condition = spec.get("condition")
            node_type = _resolve_type(spec["type"])
            # 注册表类型键与 label 写进 metadata 供 to_mermaid 展示；YAML 的
            # metadata 在后，同名 key（如自定义 label）可覆盖默认值
            dag.add_node(
                Node(
                    name=name,
                    func=node_type.func,
                    depends_on=deps,
                    retry=retry,
                    timeout=spec.get("timeout"),
                    condition=_resolve_function(condition) if condition else None,
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

    # 输入键与节点名共享 ctx 命名空间：必填/默认键重名会被节点输出覆盖，直接拒绝
    clash = sorted((set(required) | set(defaults)) & set(dag.node_names))
    if clash:
        raise ValueError(f"输入参数键与节点名冲突: {', '.join(clash)}")
    return dag
