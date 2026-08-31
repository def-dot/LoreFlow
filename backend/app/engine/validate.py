"""共享校验实现 —— 一项检查一个实现、多层调用。

布局：字段白名单 → 图结构 → 单项校验 → 汇总（validate_config）。

引用类声明统一 ``$`` 前缀并做来源校验：inputs 的 ``$`` 引用与 condition
根键 ∈ 参数键 ∪ 上游节点（condition/_review 域另含本节点 inputs 本地键——
求值/取值在接线视图上），_review 卡片键再查协议键占用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.registry import REGISTRY

from .condition import _parse


# ------------------------------------------------------------------
# 字段白名单（多余字段报「不支持的字段」）
# ------------------------------------------------------------------

#: 每个参数键接受的字段。
_PARAM_FIELDS = {"label", "description", "default", "required", "multiline", "file"}

#: 注册函数类型接受的默认字段集（human 同样使用，无特殊字段）。
_NODE_FIELDS = {"type", "label", "depends_on", "inputs", "retry", "timeout", "condition"}


# ------------------------------------------------------------------
# 图结构（依赖存在 / 环 / 上游闭包）
# ------------------------------------------------------------------


def _ancestors(name: str, edges: Mapping[str, Any]) -> set[str]:
    """depends_on 闭包（直接上游及其全部祖先）。"""
    seen: set[str] = set()
    stack = [name]
    while stack:
        for dep in edges.get(stack.pop()) or []:
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def _find_cycle(edges: Mapping[str, Sequence[str]]) -> list[str] | None:
    """DFS 环检测：``edges = {节点名: 依赖名列表}``，返回环路径或 ``None``。

    由 :func:`validate_graph` 调用：依赖图中指向不存在节点的边直接
    跳过（缺失依赖由 validate_graph 单独报错，缺失不可能成环）。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in edges}
    path_stack: list[str] = []

    def dfs(node_name: str) -> list[str] | None:
        colour[node_name] = GRAY
        path_stack.append(node_name)  # 入栈

        for dep in edges[node_name]:
            if dep not in colour:
                continue
            if colour[dep] == GRAY:
                # 发现环：从 dep 第一次出现的位置直接切片提取完整环路径
                cycle_start_idx = path_stack.index(dep)
                return path_stack[cycle_start_idx:]

            if colour[dep] == WHITE:
                cycle = dfs(dep)
                if cycle:
                    return cycle

        colour[node_name] = BLACK
        path_stack.pop()  # 出栈（回溯）
        return None

    for name in edges:
        if colour[name] == WHITE:
            cycle = dfs(name)
            if cycle:
                return cycle
    return None


def validate_graph(edges: Mapping[str, Any]) -> list[str]:
    """图结构校验（depends_on 类型 + 依赖存在性 + 环）
    """
    errors: list[str] = []
    for name, deps in edges.items():
        if not deps:  # 未声明依赖
            continue
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errors.append(f"节点 {name!r}: depends_on 必须是字符串列表")
            continue
        for dep in deps:
            if dep not in edges:
                errors.append(f"节点 {name!r} 依赖的 {dep!r} 不在 DAG 中")
    cycle = _find_cycle(
        {n: d for n, d in edges.items()
         if isinstance(d, list) and all(isinstance(x, str) for x in d)}
    )
    if cycle:
        errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
    return errors


# ------------------------------------------------------------------
# 单项校验（condition / 运行输入）
# ------------------------------------------------------------------


def _validate_wiring(
    wiring: Any,
    available_refs: set[str],
) -> list[str]:
    """inputs 声明校验：必须是映射，``$`` 引用根键 ∈ 参数键 ∪ 上游闭包。
    """
    if not isinstance(wiring, dict):
        return ["inputs 必须是「本地键: 来源键或字面量」的映射"]

    errors: list[str] = []
    for local, source in wiring.items():
        if not (isinstance(source, str) and source.startswith("$")):
            continue  # 字面量不校验来源
        root = source[1:].partition(".")[0]  # $node.field → node
        if root not in available_refs:
            errors.append(f"inputs.{local} 引用的 {root!r} 不是参数键或上游依赖节点")
    return errors


def _validate_condition(condition: Any, available_refs: set[str]) -> list[str]:
    """condition 声明校验：布尔常量（``true``/``false`` 开关）或表达式字符串。
    """
    if isinstance(condition, bool):
        return []  # 未声明（YAML ``condition:`` 空值）/ true-false 常量开关

    if not isinstance(condition, str) or not condition.strip():
        return [
            f"condition 必须是非空表达式字符串"
            f"（如 $intent == chat / $merge / not $flag），实际是 {condition!r}"
        ]
    try:
        root = _parse(condition)[1].split(".")[0]
    except ValueError as exc:
        return [exc]
    if root not in available_refs:
        return [f"condition 引用的 {root!r} 不是参数键、上游依赖节点或本地键"]
    return []


def _validate_review(review: Any, available_refs: set[str]) -> list[str]:
    """_review 卡片声明校验：``{$键: 标签文本}``，键根 ∈ available_refs。
    """
    if not isinstance(review, dict):
        return [f"_review 必须是「$键: 标签文本」的映射，实际是 {type(review).__name__}"]
    if not review:
        return ["_review 声明不能为空映射"]

    errors: list[str] = []
    for k in review:
        if not (isinstance(k, str) and k.startswith("$") and len(k) > 1):
            errors.append(f"_review 键 {k!r} 必须带 $ 引用前缀（如 $title）")
            continue
        root = k[1:].split(".")[0]
        if root in ("approve", "reason"):
            errors.append(f"_review 字段 {root!r} 不得占用协议键")
        if root not in available_refs:
            errors.append(f"_review 字段 {root!r} 不是参数键、上游依赖节点或本地键")
    return errors


def validate_inputs(
    inputs: Any, params: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """输入校验（输入键 ⊆ 声明键 + 必填缺失/为空；params 为空 = 白名单为空）
    """
    errors: list[str] = []
    invalid = sorted(set(inputs or {}) - set(params))
    if invalid:
        errors.append(f"未声明的参数键: {', '.join(invalid)}")

    missing: list[str] = []
    for name, spec in params.items():
        if not isinstance(spec, dict):
            continue
        if not spec.get("required"):
            continue
        value = (inputs or {}).get(name)
        # 未提供 = None / 空串 / 纯空白串；0/False 是填了的合法值
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    if missing:
        errors.append(f"必填参数缺失或为空: {', '.join(missing)}")
    return errors


def validate_params(config: dict[str, Any]) -> list[str]:
    """inputs 声明校验（顶层类型 + 键与节点名冲突 + 每键形状）
    """
    params = config.get("inputs")
    if params is None:
        return []
    if not isinstance(params, dict):
        return [f"params 必须是映射(dict)，实际是 {type(params).__name__}"]

    errors: list[str] = []
    clash = sorted(set(params) & set(config.get("nodes") or {}))
    if clash:
        errors.append(f"输入参数键与节点名冲突: {', '.join(clash)}")

    for name, spec in params.items():
        if not isinstance(spec, dict):
            errors.append(f"参数 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")
            continue
        unknown = set(spec) - _PARAM_FIELDS
        if unknown:
            errors.append(f"参数 {name!r}: 不支持的字段 {sorted(unknown)}")
        for bool_field in ("required", "multiline", "file"):  # None = 未设置，跳过
            value = spec.get(bool_field)
            if value is not None and not isinstance(value, bool):
                errors.append(f"参数 {name!r}: {bool_field} 必须是布尔值")
    return errors


def validate_nodes(config: dict[str, Any]) -> list[str]:
    """校验顶层 nodes 声明，返回全部错误（空列表 = 合法）。
    """
    nodes = config.get("nodes")
    if not nodes:
        return ["流水线至少需要一个节点"]
    
    if not isinstance(nodes, dict):
        return [f"nodes 必须是映射(dict)，实际是 {type(nodes).__name__}"]

    errors: list[str] = []
    params = config.get("inputs") or {}  # YAML ``inputs:`` 空值 → None

    edges: dict[str, Any] = {
        n: (s.get("depends_on") if isinstance(s, dict) else []) for n, s in nodes.items()
    }
    if errors := validate_graph(edges):
        return errors

    for name, spec in nodes.items():
        if not isinstance(spec, dict):
            errors.append(f"节点 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")
            continue

        type_key = spec.get("type")
        if not type_key:
            errors.append(f"节点 {name!r}: 需要 'type'（函数键）")
        elif type_key not in REGISTRY:
            errors.append(f"节点 {name!r}: 类型函数 {type_key!r} 未注册")

        allowed = _NODE_FIELDS

        unknown = set(spec) - allowed
        if unknown:
            errors.append(f"节点 {name!r}（{type_key}）: 不支持的字段 {sorted(unknown)}")

        refs = _ancestors(name, edges) | set(params)

        wiring = spec.get("inputs")
        if wiring:
            errors.extend(
                f"节点 {name!r}: {msg}"
                for msg in _validate_wiring(wiring, refs)
            )
        refs = refs | set(wiring) if isinstance(wiring, dict) else refs

        condition = spec.get("condition")
        if condition:
            errors.extend(f"节点 {name!r}: {msg}" for msg in _validate_condition(condition, refs))

        if type_key == "human" and isinstance(wiring, dict) and wiring.get("_review") is not None:
            errors.extend(
                f"节点 {name!r}: {msg}" for msg in _validate_review(wiring["_review"], refs)
            )

    return errors


def validate_config(config: dict[str, Any]) -> list[str]:
    """校验完整 DAG 配置（params + nodes 及其接线/condition/review/图），返回全部错误。
    """
    return validate_params(config) + validate_nodes(config)
