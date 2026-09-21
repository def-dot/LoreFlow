"""共享校验层 — Pipeline（声明式）的全部结构校验。

``Pipeline`` 调用 ``validate_pipeline()``。
``validate_inputs`` 同时被 orchestrator 调用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .condition import parse_condition

if TYPE_CHECKING:
    from .pipeline import Node

# ---------------------------------------------------------------------------
# Node 列表校验入口（Pipeline 构造期调用）
# ---------------------------------------------------------------------------

def validate_nodes(nodes: list["Node"]) -> list[str]:
    """``list[Node]`` 校验入口（Pipeline 构造期调用）。

    先逐节点校验，再图结构校验（节点名去重、依赖存在性、环检测）。
    """
    errors: list[str] = []
    for node in nodes:
        errors.extend(_validate_node(node, nodes=nodes))
    errors.extend(_validate_graph(nodes))
    return errors


# ---------------------------------------------------------------------------
# 单节点校验
# ---------------------------------------------------------------------------
def _validate_node_condition(
    node: "Node",
    nodes: dict[str, Node],
) -> list[str]:
    """校验节点 ``condition`` 属性：类型、表达式语法、引用来源。"""
    if node.condition is None or isinstance(node.condition, bool):
        return []

    errors: list[str] = []
    if not isinstance(node.condition, str):
        errors.append(f"节点 {node.name!r}: condition 类型必须是 str 或 bool，实际是 {type(node.condition).__name__}")
        return errors

    # 空字符串
    condition = node.condition.strip()
    if not condition:
        errors.append(f"节点 {node.name!r}: condition 不能为空字符串")
        return errors

    # 表达式语法校验 + 引用来源校验（参数键 / 上游依赖节点）
    try:
        groups = parse_condition(condition)
    except ValueError as exc:
        errors.append(f"节点 {node.name!r}: {exc}")
        return errors

    upstream = _get_upstream_nodes(node, nodes)
    for and_group in groups:
        for _, key, _, _ in and_group:
            errors.extend(
                f"节点 {node.name!r}: condition {msg}"
                for msg in _check_ref(f"${key}", upstream, nodes)
            )
    return errors


def _validate_node(
    node: "Node",
    nodes: list["Node"],
) -> list[str]:
    """单节点字段级校验。传入 nodes/edges 时额外校验 inputs 中的 $ 引用。"""
    from app.registry import REGISTRY

    errors: list[str] = []
    name = node.name

    # type 必须在 REGISTRY 中注册
    if node.type not in REGISTRY:
        errors.append(f"节点 {name!r}: 未知的 type {node.type!r}")
        return errors

    nodes_dict = {item.name: item for item in nodes}

    errors.extend(_validate_node_inputs(node, nodes_dict))
    errors.extend(_validate_node_condition(node, nodes_dict))

    # retry 校验
    retry = node.retry
    if isinstance(retry, int) and retry < 0:
        errors.append(f"节点 {name!r}: retry 不能为负数，实际是 {retry}")

    # timeout 校验
    timeout = node.timeout
    if timeout is not None and timeout <= 0:
        errors.append(f"节点 {name!r}: timeout 必须为正数，实际是 {timeout}")

    return errors


# ---------------------------------------------------------------------------
# $引用校验公共方法
# ---------------------------------------------------------------------------

def _get_upstream_nodes(node: "Node", nodes: dict[str, Node]) -> set[str]:
    """返回 node 的所有上游节点（传递闭包）。"""
    seen: set[str] = set()
    stack = list(node.depends_on or [])
    while stack:
        dep = stack.pop()
        if dep not in seen:
            seen.add(dep)
            stack.extend(
                getattr(nodes.get(dep), "depends_on", None) or []
            )
    return seen


def _check_ref(
    ref: str,
    upstream: set[str],
    nodes: dict[str, Node],
) -> list[str]:
    """校验单个 $引用：上游节点存在性 + 字段存在性。

    ``ref`` 形如 ``"$node.field"`` 或 ``"$node"``。
    ``upstream`` 是当前节点的上游依赖节点名集合（start 隐式包含）。
    ``nodes`` 是全图节点映射，用于读取上游节点类型。
    """
    raw_root, _, field = ref.partition(".")
    root = raw_root.lstrip("$")
    if root not in upstream:
        return [f"引用的 {root!r} 不是上游依赖节点"]
    if not field:
        return [f"引用 {root!r} 缺少字段名"]
    up_node = nodes.get(root)
    out_schema = up_node.resolve_output_schema()
    if out_schema is not None:
        top_field = field.split(".")[0]
        if top_field not in out_schema.model_fields:
            return [f"引用的 {root!r} 输出中没有字段 {top_field!r}"]
    return []


def _validate_node_inputs(
    node: "Node",
    nodes: dict[str, Node] | None = None,
) -> list[str]:
    """节点 inputs 校验（start 参数声明 + 其他类型 input_schema + $引用）。"""
    from app.registry import REGISTRY

    errors: list[str] = []
    inputs = node.inputs or {}

    # ---- inputs 校验 ----
    if node.type == "start":
        from pydantic import ValidationError
        from .pipeline import InputParamDef
        for key, val in inputs.items():
            try:
                InputParamDef.model_validate(val)
            except ValidationError as exc:
                detail = "; ".join(e["msg"] for e in exc.errors())
                errors.append(f"节点 {node.name!r}: start 参数 {key!r} 定义无效 — {detail}")
    else:
        func_def = REGISTRY.get(node.type)
        schema = func_def.input_schema if func_def else None
        fields = schema.model_fields if schema else {}
        for key in fields:
            if fields[key].is_required() and key not in inputs:
                errors.append(f"节点 {node.name!r}: inputs 缺少必填参数 {key!r}")
        if unexpected := set(inputs) - set(fields):
            errors.append(f"节点 {node.name!r}: inputs 包含未知参数 {unexpected!r}")

    # ---- $引用校验 ----
    upstream = _get_upstream_nodes(node, nodes)
    for key, val in inputs.items():
        if isinstance(val, str) and val.startswith("$"):
            errors.extend(
                f"节点 {node.name!r}: inputs {msg}"
                for msg in _check_ref(val, upstream, nodes)
            )

    return errors


def _validate_graph(nodes: list['Node']) -> list[str]:
    """依赖存在性 + 环检测。接受 ``list[Node]``。"""
    names = [n.name for n in nodes]
    edges: dict[str, list[str]] = {n.name: n.depends_on for n in nodes}
    errors: list[str] = []

    # ---- 节点名重复检测 ----
    from collections import Counter
    dupes = sorted(name for name, cnt in Counter(names).items() if cnt > 1)
    if dupes:
        errors.append(f"节点名重复: {', '.join(dupes)}")

    # ---- 依赖存在性 ----
    for node in nodes:
        for dep in node.depends_on:
            if dep not in names:
                errors.append(f"节点 {node.name!r} 依赖的 {dep!r} 不在 DAG 中")

    # ---- DFS 环检测 ----
    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in edges}
    path_stack: list[str] = []

    def dfs(node_name: str) -> list[str] | None:
        colour[node_name] = GRAY
        path_stack.append(node_name)
        for dep in edges[node_name]:
            if dep not in colour:
                continue
            if colour[dep] == GRAY:
                return path_stack[path_stack.index(dep):]
            if colour[dep] == WHITE:
                cycle = dfs(dep)
                if cycle:
                    return cycle
        colour[node_name] = BLACK
        path_stack.pop()
        return None

    for name in edges:
        if colour[name] == WHITE:
            cycle = dfs(name)
            if cycle:
                errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
                break

    return errors


# ---------------------------------------------------------------------------
# 运行时输入校验
# ---------------------------------------------------------------------------

def validate_inputs(
    inputs: dict[str, Any],
    declared: dict[str, Any],
) -> list[str]:
    """校验运行时 ``inputs`` 是否符合 ``__start__`` 参数声明。

    ``declared`` 的值是 dict（required / default / …）。
    """
    errors: list[str] = []
    if declared is None:
        declared = {}

    declared_keys = set(declared)
    provided_keys = set(inputs) if inputs else set()

    if declared_keys:
        undeclared = sorted(provided_keys - declared_keys)
    else:
        undeclared = sorted(provided_keys)
    if undeclared:
        errors.append(f"未声明的参数键: {', '.join(undeclared)}")

    if declared:
        for key, schema in declared.items():
            if not schema.get("required"):
                continue
            if key in inputs:
                val = inputs[key]
                if val is None or (isinstance(val, str) and not val.strip()):
                    errors.append(f"必填参数缺失或为空: {key}")
            elif schema.get("default") is None:
                errors.append(f"必填参数缺失或为空: {key}")

    return errors
