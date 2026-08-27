"""共享校验实现 —— 一项检查一个实现、多层调用。

布局：字段白名单 → 图结构 → 数据流接线 → 单项校验 → 汇总（validate_config）。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from app.registry import REGISTRY

from .condition import condition_key


# ------------------------------------------------------------------
# 字段白名单（多余字段报「不支持的字段」）
# ------------------------------------------------------------------

#: 每个参数键接受的字段。
_PARAM_FIELDS = {"label", "description", "default", "required", "multiline", "file"}

#: 每种节点类型接受的字段。
_KIND_FIELDS = {
    "node": {"type", "label", "depends_on", "inputs", "retry", "timeout", "condition", "metadata"},
    "human": {"label", "depends_on", "inputs", "retry", "prompt", "condition", "review"},
    "loop": {"label", "depends_on", "retry", "timeout", "condition", "body", "max_iterations"},
}


# ------------------------------------------------------------------
# 图结构（依赖存在 / 环 / 上游闭包）
# ------------------------------------------------------------------


def find_cycle(edges: Mapping[str, Sequence[str]]) -> list[str] | None:
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
        if deps is None:  # 未声明依赖
            continue
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errors.append(f"节点 {name!r}: depends_on 必须是字符串列表")
            continue
        for dep in deps:
            if dep not in edges:
                errors.append(f"节点 {name!r} 依赖的 {dep!r} 不在 DAG 中")
    cycle = find_cycle(  # 指向不存在节点的边跳过（缺失依赖上面已报，缺失不可能成环）
        {n: d for n, d in edges.items()
         if isinstance(d, list) and all(isinstance(x, str) for x in d)}
    )
    if cycle:
        errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
    return errors


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


# ------------------------------------------------------------------
# 数据流接线（inputs：$引用 或字面量）
# ------------------------------------------------------------------


def _ref_roots(wiring: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """接线里的 ``$`` 引用项，产出 ``(本地键, 根键)``；字面量跳过。"""
    for local, source in wiring.items():
        if isinstance(source, str) and source.startswith("$"):
            yield local, source[1:].partition(".")[0]  # $node.field → node


def validate_wiring(wiring: Any, name: str, available: set[str]) -> list[str]:
    """节点 inputs 接线校验：必须是映射，``$`` 引用根键 ∈ 节点名 ∪ 参数键。"""
    if not isinstance(wiring, dict):
        return [f"节点 {name!r}: inputs 必须是「本地键: 来源键或字面量」的映射"]
    return [
        f"节点 {name!r}: inputs.{local} 引用的 {root!r} 不是节点名或参数键"
        for local, root in _ref_roots(wiring)
        if root not in available
    ]


def validate_wiring_upstream(
    wirings: Mapping[str, Mapping[str, Any]], edges: Mapping[str, Any]
) -> list[str]:
    """接线来源若是节点，必须在声明者的 depends_on 上游链中
    （数据只能来自已声明的上游或参数；根键是参数的引用不受此限）。"""
    errors: list[str] = []
    for name, wiring in wirings.items():
        ancestors = _ancestors(name, edges)
        for local, root in _ref_roots(wiring):
            if root in edges and root not in ancestors:
                errors.append(
                    f"节点 {name!r}: inputs.{local} 引用的 {root!r} 不在 depends_on 上游链中"
                )
    return errors


# ------------------------------------------------------------------
# 单项校验（review / condition / 运行输入）
# ------------------------------------------------------------------


def validate_review(review: Any, available: Iterable[str]) -> list[str]:
    """review 声明校验（顶层类型 + 非空 + 键引用 + 字段格式）
    """
    if not isinstance(review, dict):
        return [f"review 必须是映射，实际是 {type(review).__name__}"]
    if not review:
        return ["review 声明不能为空映射"]

    errors: list[str] = []
    unknown = [k for k in review if k not in available]
    if unknown:
        errors.append(f"review 引用了未声明的键 {', '.join(unknown)}")

    for key, val in review.items():
        if not isinstance(val, dict):
            errors.append(
                f"review 字段 {key!r}: 必须是 {{label: 文本}} 格式，实际是 {type(val).__name__}"
            )
            continue

        unsupported = set(val) - {"label"}
        if unsupported:
            errors.append(f"review 字段 {key!r}: 不支持的字段 {sorted(unsupported)}")

        label = val.get("label")
        if not isinstance(label, str):
            errors.append(f"review 字段 {key!r}: label 必须是字符串")
    return errors


def validate_condition(
    condition: Any, name: str, kind: str, available: set[str] | None = None
) -> list[str]:
    """condition 声明校验：必须是表达式字符串（``intent == chat`` / ``merge``）。

    表达式语法与引用键都在载入期核对 —— 键 = params ∪ 节点名 ∪ 本节点
    ``inputs`` 本地键（loop 另有 ``iteration``）。拼错的键在运行期只会
    静默取 None（比较类表达式恒 False，节点被跳过），必须提前报出来。
    """
    if not isinstance(condition, str) or not condition.strip():
        return [
            f"节点 {name!r}（{kind}）: condition 必须是非空表达式字符串"
            f"（如 intent == chat / merge / not flag），实际是 {condition!r}"
        ]
    try:
        root = condition_key(condition)
    except ValueError as exc:
        return [f"节点 {name!r}（{kind}）: {exc}"]
    if available is not None and root not in available:
        return [
            f"节点 {name!r}（{kind}）: condition 引用的 {root!r}"
            f" 不是参数键、节点名或 inputs 本地键"
        ]
    return []


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
        if not isinstance(spec, dict):  # 畸形声明由 validate_params 报，这里跳过派生
            continue
        if not spec.get("required"):
            continue
        value = (inputs or {}).get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    if missing:
        errors.append(f"必填参数缺失或为空: {', '.join(missing)}")
    return errors


# ------------------------------------------------------------------
# 汇总（config 级）
# ------------------------------------------------------------------


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
    params = config.get("inputs")
    available_ref = set(nodes) | (set(params) if isinstance(params, dict) else set())

    edges: dict[str, Any] = {}
    wirings: dict[str, dict[str, Any]] = {}  # 各节点接线，供 validate_wiring_upstream 查上游链
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

        edges[name] = spec.get("depends_on")

        # 数据流接线：{本地键: $来源键或字面量}，先于 condition 校验（哪些 kind 有 inputs 由白名单决定）
        wiring = spec.get("inputs") if "inputs" in allowed else None
        if wiring:
            errors.extend(validate_wiring(wiring, name, available_ref))
            if isinstance(wiring, dict):
                wirings[name] = wiring

        condition_key = spec.get("condition")
        if condition_key is not None:
            # 条件可引用的键 = params ∪ 节点名 ∪ 本节点 inputs 本地键（接线畸形时为空）
            cond_available = available_ref | (set(wiring) if isinstance(wiring, dict) else set())
            if kind == "loop":
                cond_available |= {"iteration"}  # 循环谓词视图每轮注入 iteration
            errors.extend(validate_condition(condition_key, name, kind, cond_available))

        # kind 特定必需字段校验
        if kind == "node":
            type_key = spec.get("type")
            if not type_key:
                errors.append(f"节点 {name!r}: 需要 'type'（函数键）")
            elif type_key not in REGISTRY:
                errors.append(f"节点 {name!r}（node）: 类型函数 {type_key!r} 未注册")
        elif kind == "human":
            review_spec = spec.get("review")
            if review_spec is not None:
                errors.extend(
                    f"审核节点 {name!r}: {msg}" for msg in validate_review(review_spec, available_ref)
                )
        elif kind == "loop":
            body = spec.get("body")
            if not isinstance(body, dict) or not body:
                errors.append(f"循环节点 {name!r}: 需要非空的 'body' 映射")
            if not condition_key:
                errors.append(f"循环节点 {name!r}: 需要 'condition' 表达式")
            elif isinstance(body, dict) and body:
                errors.extend(
                    f"循环节点 {name!r}: {msg}"
                    for msg in validate_nodes({"nodes": body})
                )

    errors.extend(validate_graph(edges))
    errors.extend(validate_wiring_upstream(wirings, edges))
    return errors


def validate_config(config: dict[str, Any]) -> list[str]:
    """校验完整 DAG 配置（params + nodes 及其接线/condition/review/图），返回全部错误。
    """
    return validate_params(config) + validate_nodes(config)
