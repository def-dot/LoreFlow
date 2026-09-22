"""校验层（构造期，pydantic 校验之后）。

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
    from .pipeline import Node


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

def validate_ref(nodes: list["Node"]) -> list[str]:
    """``list[Node]`` $引用校验入口（Pipeline 构造期调用）。"""
    from app.registry import REGISTRY

    nodes_dict: dict[str, Node] = {n.name: n for n in nodes}
    errors: list[str] = []
    for node in nodes:
        upstream = _get_upstream_nodes(node, nodes_dict)
        # inputs $引用（递归遍历 dict / list）
        for ref in _collect_refs(node.inputs):
            for msg in _iter_ref_errors(ref, upstream, nodes_dict):
                errors.append(f"节点 {node.name!r}: inputs {msg}")
        # condition $引用
        if node.condition is not None and not isinstance(node.condition, bool):
            groups = parse_condition(node.condition)
            for and_group in groups:
                for _, key, _, _ in and_group:
                    for msg in _iter_ref_errors(f"${key}", upstream, nodes_dict):
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


def _iter_ref_errors(
    ref: str,
    upstream: set[str],
    nodes_dict: dict[str, "Node"],
) -> Iterator[str]:
    """校验单个 $引用，yield 错误消息。"""
    raw_root, _, field = ref.partition(".")
    root = raw_root.lstrip("$")
    if root not in upstream:
        yield f"引用的 {root!r} 不是上游依赖节点"
        return
    if not field:
        yield f"引用 {root!r} 缺少字段名"
        return
    up_node = nodes_dict.get(root)
    out_schema = up_node.resolve_output_schema()
    if out_schema is not None:
        top_field = field.split(".")[0]
        if top_field not in out_schema.model_fields:
            yield f"引用的 {root!r} 输出中没有字段 {top_field!r}"
