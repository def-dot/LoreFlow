"""共享校验实现 —— 一项检查一个实现、多层调用。
"""

from __future__ import annotations

import inspect
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
_PARAM_FIELDS = {"label", "description", "default", "required", "multiline", "file"}


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
        file_flag = spec.get("file", False)
        if not isinstance(file_flag, bool):
            errors.append(f"参数 {name!r}: file 必须是布尔值")
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


def validate_condition(condition: Any, name: str, kind: str) -> list[str]:
    """condition 声明校验：函数名字符串，或 ``{fn: 键, 其余键: 参数}`` 映射。

    映射形式的参数与函数签名绑定核对——条件在执行期抛异常会被按
    「不执行」吞掉（executor 策略），拼错的参数键必须在载入期报出来。
    loop 的 condition 是循环谓词（多一个 iteration 参数），只接受字符串。
    """
    if isinstance(condition, str):
        if condition not in REGISTRY:
            return [f"节点 {name!r}（{kind}）: 条件函数 {condition!r} 未注册"]
        return []
    if not isinstance(condition, dict):
        return [
            f"节点 {name!r}（{kind}）: condition 必须是函数名字符串或 {{fn: 键, 参数…}} 映射，"
            f"实际是 {type(condition).__name__}"
        ]
    if kind == "loop":
        return [f"节点 {name!r}（loop）: condition 必须是函数名字符串（映射参数形式不支持 loop）"]
    fn_key = condition.get("fn")
    if not isinstance(fn_key, str) or not fn_key:
        return [f"节点 {name!r}（{kind}）: condition 映射需要 'fn'（条件函数键）"]
    fn_type = REGISTRY.get(fn_key)
    if fn_type is None:
        return [f"节点 {name!r}（{kind}）: 条件函数 {fn_key!r} 未注册"]
    args = {k: v for k, v in condition.items() if k != "fn"}
    try:
        inspect.signature(fn_type.func).bind({}, **args)
    except TypeError as exc:
        return [f"节点 {name!r}（{kind}）: 条件参数与 {fn_key!r} 签名不符: {exc}"]
    return []
