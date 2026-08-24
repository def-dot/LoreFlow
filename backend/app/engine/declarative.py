"""
Declarative configuration layer for DAG Flow.
"""

from __future__ import annotations

import yaml
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.registry import REGISTRY, NodeType

from . import validate
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


def validate_nodes(config: dict[str, Any]) -> list[str]:
    """校验顶层 nodes 声明，返回全部错误（空列表 = 合法）。

    依赖存在性与环在 edges 收集后委托共享的 validate_graph（与
    DAG.validate 同一实现）；loop 的 body 是独立命名空间，递归走同一
    入口（不传 params，与构建期嵌套 load_dag 的语义一致）。
    """
    nodes = config.get("nodes")
    if not nodes:
        return ["流水线至少需要一个节点"]
    if not isinstance(nodes, dict):
        return [f"nodes 必须是映射(dict)，实际是 {type(nodes).__name__}"]

    errors: list[str] = []
    params = config.get("params")
    # 可用键集合 = 参数键 + 节点名（params 类型错误已在 validate.validate_params 报告）
    available = set(nodes) | (set(params) if isinstance(params, dict) else set())

    edges: dict[str, Any] = {}
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

        # depends_on 的类型/引用存在性/环统一由 validate_graph 查（None = 未声明）
        edges[name] = spec.get("depends_on")

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
                    f"审核节点 {name!r}: {msg}" for msg in validate.validate_review(review_spec, available)
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

    errors.extend(validate.validate_graph(edges))
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
    return (
        validate.validate_params(config.get("params"), config.get("nodes") or {})
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
        params=config.get("params") or {},
        on_event=on_event,
    )

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
