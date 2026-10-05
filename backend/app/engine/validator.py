"""校验层（构造期，pydantic 校验之后）。
    ~a`
- ``validate_dag(nodes)`` — 图结构：节点名去重、依赖存在性、环检测。
- ``validate_ref(nodes)`` — inputs / condition 中 $引用的来源合法性。

字段级校验（type 注册、timeout>0、condition 非空白与语法、inputs schema）
由 pydantic 在 Node 字段 / model 校验期完成。
图结构校验（节点名去重、依赖存在性、环检测）由 Pipeline.model_validator 完成。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from .condition import parse_condition

if TYPE_CHECKING:
    from .pipeline import Node, Pipeline


# ------------------------------------------------------------------
# 图结构校验
# ------------------------------------------------------------------

def validate_dag(nodes: list["Node"]) -> list[str]:
    """图结构校验：节点名去重、依赖存在性、DFS 环检测。"""
    errors: list[str] = []
    names = [n.name for n in nodes]

    # 节点名去重
    dupes = sorted(name for name, cnt in Counter(names).items() if cnt > 1)
    if dupes:
        errors.append(f"节点名重复: {', '.join(dupes)}")

    name_set = set(names)

    # 依赖存在性
    for node in nodes:
        for dep in node.depends_on:
            if dep not in name_set:
                errors.append(f"节点 {node.name!r} 依赖的 {dep!r} 不在 DAG 中")

    # DFS 环检测
    deps: dict[str, list[str]] = {n.name: n.depends_on for n in nodes}
    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in deps}
    path_stack: list[str] = []

    def dfs(name: str) -> list[str] | None:
        colour[name] = GRAY
        path_stack.append(name)
        for dep in deps[name]:
            if dep not in colour:
                continue
            if colour[dep] == GRAY:
                return path_stack[path_stack.index(dep) :]
            if colour[dep] == WHITE:
                cycle = dfs(dep)
                if cycle:
                    return cycle
        colour[name] = BLACK
        path_stack.pop()
        return None

    for name in deps:
        if colour[name] == WHITE:
            cycle = dfs(name)
            if cycle:
                errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
                break

    return errors


# ------------------------------------------------------------------
# $参数引用校验
# ------------------------------------------------------------------

def validate_ref(pipeline: "Pipeline") -> list[str]:
    """$引用校验入口（Pipeline 构造期调用）。"""
    nodes_dict: dict[str, Node] = {n.name: n for n in pipeline.nodes}
    errors: list[str] = []
    for node in pipeline.nodes:
        upstream = _get_upstream_nodes(node, nodes_dict)
        for ref in _collect_refs(node.inputs):
            for msg in _iter_ref_errors(ref, upstream, pipeline):
                errors.append(f"节点 {node.name!r}: inputs {msg}")
        if node.condition is not None and not isinstance(node.condition, bool):
            groups = parse_condition(node.condition)
            for and_group in groups:
                for _, key, _, _ in and_group:
                    for msg in _iter_ref_errors(f"${key}", upstream, pipeline):
                        errors.append(f"节点 {node.name!r}: condition {msg}")
    return errors


def _get_upstream_nodes(
    node: "Node", nodes_dict: dict[str, "Node"]
) -> set[str]:
    """返回 node 的所有上游节点（传递闭包）。"""
    seen: set[str] = set()
    stack = list(node.depends_on or [])
    while stack:
        dep = stack.pop()
        if dep not in seen:
            seen.add(dep)
            stack.extend(
                getattr(nodes_dict.get(dep), "depends_on", None) or []
            )
    return seen


def _collect_refs(value: Any) -> Iterator[str]:
    """递归收集 value 中所有 $引用字符串。"""
    if isinstance(value, str):
        if value.startswith("$"):
            yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _collect_refs(v)
    elif isinstance(value, list):
        for v in value:
            yield from _collect_refs(v)


def resolve_ref_schema(ref: str, pipeline: "Pipeline") -> dict[str, Any] | None:
    """解析 $引用，返回对应字段的 JSON Schema。无输出声明或解析失败返回 None。"""
    parts = ref.lstrip("$").split(".")
    root = parts[0]
    segments = parts[1:]

    # 获取根 schema
    if root == "params":
        if not pipeline.params:
            return None
        root_schema = {
            "type": "object",
            "properties": {k: v.model_dump() for k, v in pipeline.params.items()},
        }
    else:
        nodes_dict = {n.name: n for n in pipeline.nodes}
        node = nodes_dict.get(root)
        if not node:
            return None
        from app.registry import REGISTRY
        func_def = REGISTRY.get(node.type)
        root_schema = func_def.json_output_schema() if func_def else None
        if not root_schema:
            return None

    # $task_a — 返回完整 schema
    if not segments:
        return root_schema

    # 沿路径逐层取 properties
    schema: dict[str, Any] = root_schema
    for seg in segments:
        props = schema.get("properties")
        if isinstance(props, dict) and seg in props:
            schema = props[seg]
        else:
            return None
    return schema


def _iter_ref_errors(
    ref: str,
    upstream: set[str],
    pipeline: "Pipeline",
) -> Iterator[str]:
    """校验单个 $引用，yield 错误消息。"""
    parts = ref.lstrip("$").split(".")
    root = parts[0]
    if root != "params" and root not in upstream:
        yield f"引用的 {root!r} 不是上游依赖节点"
        return
    if resolve_ref_schema(ref, pipeline) is None:
        yield f"引用 {ref!r} 无效"
