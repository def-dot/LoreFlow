"""共享校验实现 —— 一项检查一个实现、多层调用。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.registry.core import REGISTRY


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
    deps_missing = False
    for name, deps in edges.items():
        if deps is None:  # 未声明依赖
            continue
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            errors.append(f"节点 {name!r}: depends_on 必须是字符串列表")
            continue
        for dep in deps:
            if dep not in edges:
                errors.append(f"节点 {name!r} 依赖的 {dep!r} 不在 DAG 中")
                deps_missing = True
    if not deps_missing:
        cycle = find_cycle(
            {n: d for n, d in edges.items()
             if isinstance(d, list) and all(isinstance(x, str) for x in d)}
        )
        if cycle:
            errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
    return errors


#: Fields accepted per key in a ``params`` mapping; anything else raises.
_PARAM_FIELDS = {"label", "description", "default", "required", "multiline"}


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


def validate_params(params: Any, node_names: Iterable[str] = ()) -> list[str]:
    """params 声明校验（顶层类型 + 键与节点名冲突 + 每键形状）
    """
    if params is None:
        return []
    if not isinstance(params, dict):
        return [f"params 必须是映射(dict)，实际是 {type(params).__name__}"]

    errors: list[str] = []
    clash = sorted(set(params) & set(node_names))
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


#: Fields accepted in a ``routes`` mapping; anything else raises.
_ROUTE_FIELDS = {"router", "branches", "default"}


def validate_routes(nodes: Mapping[str, Any]) -> list[str]:
    """routes 声明校验（边级路由：router/branches/default 与支路成员约束）。

    由 :func:`validate_nodes` 调用，``nodes`` 为（顶层或 loop body 的）
    节点声明映射。成员规则：
    - 必须是已声明节点，且不能是路由源自身或 loop 节点（loop 的
      condition 是循环谓词，与支路门卫语义不同）
    - 不能再声明 condition（路由门卫与显式条件互斥）
    - 一个节点只能属于一条支路（同一源或不同源都不行）
    - 必须（传递）依赖路由源 —— 门卫在依赖就绪后求值，读的是源输出
    - 直接依赖不能落在同源的兄弟支路里（跨支路依赖）
    """
    errors: list[str] = []

    # 结构规整的 depends_on 才参与可达性/跨支路检查（畸形依赖另有报错）
    deps_of: dict[str, list[str]] = {
        name: deps
        for name, spec in nodes.items()
        if isinstance(spec, dict)
        and isinstance((deps := spec.get("depends_on") or []), list)
        and all(isinstance(d, str) for d in deps)
    }
    missing_dep = any(d not in nodes for deps in deps_of.values() for d in deps)

    membership: dict[str, tuple[str, str]] = {}  # 支路成员 -> (路由源, 标签)
    for name, spec in nodes.items():
        routes = spec.get("routes") if isinstance(spec, dict) else None
        if routes is None:
            continue
        if not isinstance(routes, dict):
            errors.append(f"节点 {name!r}: routes 必须是映射(dict)，实际是 {type(routes).__name__}")
            continue
        unknown = set(routes) - _ROUTE_FIELDS
        if unknown:
            errors.append(f"节点 {name!r}: routes 不支持的字段 {sorted(unknown)}")

        router_key = routes.get("router")
        if not isinstance(router_key, str) or not router_key:
            errors.append(f"节点 {name!r}: routes 需要 'router'（路由函数键）")
            continue
        router_type = REGISTRY.get(router_key)
        if router_type is None:
            errors.append(f"节点 {name!r}: 路由函数 {router_key!r} 未注册")
        elif router_type.kind != "router":
            errors.append(
                f"节点 {name!r}: {router_key!r} 是 {router_type.kind} 类型，routes.router 必须是 router 类型函数"
            )

        branches = routes.get("branches")
        if not isinstance(branches, dict) or not branches:
            errors.append(f"节点 {name!r}: routes 需要非空的 'branches' 映射（{{标签: [成员节点]}}）")
            continue
        for label, members in branches.items():
            if not isinstance(label, str) or not label:
                errors.append(f"节点 {name!r}: 支路标签必须是非空字符串，实际是 {label!r}")
                continue
            if not isinstance(members, list) or not members or not all(isinstance(m, str) for m in members):
                errors.append(f"节点 {name!r}: 支路 {label!r} 的成员必须是非空字符串列表")
                continue
            for member in members:
                mspec = nodes.get(member)
                if not isinstance(mspec, dict):
                    errors.append(f"节点 {name!r}: 支路 {label!r} 的成员 {member!r} 不在节点声明中")
                    continue
                if member == name:
                    errors.append(f"节点 {name!r}: 支路 {label!r} 的成员不能是路由源自身")
                    continue
                if mspec.get("kind", "node") == "loop":
                    errors.append(f"节点 {name!r}: 支路 {label!r} 的成员 {member!r} 是 loop 节点（其 condition 是循环谓词，不能作支路门卫）")
                    continue
                if mspec.get("condition") is not None:
                    errors.append(f"节点 {name!r}: 支路 {label!r} 的成员 {member!r} 已声明 condition（路由门卫与显式条件互斥）")
                if member in membership:
                    other_src, other_label = membership[member]
                    where = f"支路 {other_label!r}" if other_src == name else f"{other_src!r} 的支路 {other_label!r}"
                    errors.append(f"节点 {name!r}: {member!r} 重复出现在支路中（已属于{where}；一个节点只能属于一条支路）")
                    continue
                membership[member] = (name, label)

        default = routes.get("default")
        if default is not None and default not in branches:
            errors.append(f"节点 {name!r}: default {default!r} 不是已声明的支路标签")

    if missing_dep or not membership:
        return errors  # 依赖缺失另有报错；可达性检查等上下文齐全再查

    def reaches(start: str, target: str) -> bool:
        """沿 depends_on 向上可达（visited 防环；环另有报错）。"""
        stack, seen = list(deps_of.get(start, [])), {start}
        while stack:
            cur = stack.pop()
            if cur == target:
                return True
            if cur in seen or cur not in deps_of:
                continue
            seen.add(cur)
            stack.extend(deps_of[cur])
        return False

    for member, (src, label) in membership.items():
        if not reaches(member, src):
            errors.append(
                f"节点 {src!r}: 支路 {label!r} 的成员 {member!r} 未（传递）依赖路由源 —— 门卫须在源输出就绪后求值"
            )
        for dep in deps_of.get(member, []):
            other = membership.get(dep)
            if other and other[0] == src and other[1] != label:
                errors.append(
                    f"节点 {src!r}: 支路 {label!r} 的成员 {member!r} 依赖了兄弟支路 {other[1]!r} 的 {dep!r}（跨支路依赖）"
                )
    return errors
