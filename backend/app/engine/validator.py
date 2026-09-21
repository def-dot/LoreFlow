"""校验层 — Pipeline 结构校验（构造期）。

``PipeLineValidator.validate(nodes)`` — 节点字段 + 图结构校验。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from typing import TYPE_CHECKING


from .condition import parse_condition

if TYPE_CHECKING:
    from .pipeline import Node


class PipeLineValidator:
    """Pipeline 声明式结构校验（构造期）。

    用法::

        errors = PipeLineValidator.validate(nodes)
        if errors:
            raise ValueError("\\n".join(errors))

    全部 ``@staticmethod`` — 校验是无状态操作，类仅作命名空间。
    内部分两层：

    1. 单节点校验（``_validate_node``）— type 注册、inputs schema、
       condition 语法与引用、retry / timeout 范围。
    2. 图结构校验（``_validate_graph``）— 节点名去重、依赖存在性、环检测。
    """

    # ------------------------------------------------------------------
    # 公开入口
    # ------------------------------------------------------------------

    @staticmethod
    def validate(nodes: list["Node"]) -> list[str]:
        """``list[Node]`` 校验入口（Pipeline 构造期调用）。"""
        nodes_dict: dict[str, Node] = {n.name: n for n in nodes}
        errors: list[str] = []
        for node in nodes:
            errors.extend(PipeLineValidator._validate_node(node, nodes_dict))
        errors.extend(PipeLineValidator._validate_graph(nodes))
        return errors

    # ------------------------------------------------------------------
    # 单节点校验
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_node(
        node: "Node",
        nodes_dict: dict[str, "Node"],
    ) -> list[str]:
        """单节点字段级校验。"""
        from app.registry import REGISTRY

        errors: list[str] = []
        name = node.name

        if node.type not in REGISTRY:
            errors.append(f"节点 {name!r}: 未知的 type {node.type!r}")
            return errors

        upstream = PipeLineValidator._get_upstream_nodes(node, nodes_dict)
        errors.extend(PipeLineValidator._validate_node_inputs(node, nodes_dict, upstream))
        errors.extend(PipeLineValidator._validate_node_condition(node, nodes_dict, upstream))

        if isinstance(node.retry, int) and node.retry < 0:
            errors.append(f"节点 {name!r}: retry 不能为负数，实际是 {node.retry}")

        if node.timeout is not None and node.timeout <= 0:
            errors.append(f"节点 {name!r}: timeout 必须为正数，实际是 {node.timeout}")

        return errors

    # ------------------------------------------------------------------
    # condition 校验
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_node_condition(
        node: "Node",
        nodes_dict: dict[str, "Node"],
        upstream: set[str],
    ) -> list[str]:
        """校验节点 ``condition`` 属性：类型、表达式语法、引用来源。"""
        if node.condition is None or isinstance(node.condition, bool):
            return []

        if not isinstance(node.condition, str):
            return [
                f"节点 {node.name!r}: condition 类型必须是 str 或 bool，"
                f"实际是 {type(node.condition).__name__}"
            ]

        condition = node.condition.strip()
        if not condition:
            return [f"节点 {node.name!r}: condition 不能为空字符串"]

        try:
            groups = parse_condition(condition)
        except ValueError as exc:
            return [f"节点 {node.name!r}: {exc}"]

        errors: list[str] = []
        for and_group in groups:
            for _, key, _, _ in and_group:
                for msg in PipeLineValidator._iter_ref_errors(f"${key}", upstream, nodes_dict):
                    errors.append(f"节点 {node.name!r}: condition {msg}")
        return errors

    # ------------------------------------------------------------------
    # inputs 校验
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_node_inputs(
        node: "Node",
        nodes_dict: dict[str, "Node"],
        upstream: set[str],
    ) -> list[str]:
        """节点 inputs 校验（start 参数声明 + 其他类型 input_schema + $引用）。"""
        from app.registry import REGISTRY

        errors: list[str] = []
        inputs = node.inputs or {}

        # ---- 参数声明 / schema 校验 ----
        if node.type == "start":
            from pydantic import ValidationError
            from .pipeline import InputParamDef

            for key, val in inputs.items():
                try:
                    InputParamDef.model_validate(val)
                except ValidationError as exc:
                    detail = "; ".join(e["msg"] for e in exc.errors())
                    errors.append(
                        f"节点 {node.name!r}: start 参数 {key!r} 定义无效 — {detail}"
                    )
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
        for key, val in inputs.items():
            if isinstance(val, str) and val.startswith("$"):
                for msg in PipeLineValidator._iter_ref_errors(val, upstream, nodes_dict):
                    errors.append(f"节点 {node.name!r}: inputs {msg}")

        return errors

    # ------------------------------------------------------------------
    # 图结构校验
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_graph(nodes: list["Node"]) -> list[str]:
        """依赖存在性 + 环检测。"""
        errors: list[str] = []
        deps: dict[str, list[str]] = {n.name: n.depends_on for n in nodes}
        names = set(deps)

        # ---- 节点名重复 ----
        dupes = sorted(
            name for name, cnt in Counter(n.name for n in nodes).items() if cnt > 1
        )
        if dupes:
            errors.append(f"节点名重复: {', '.join(dupes)}")

        # ---- 依赖存在性 ----
        for node in nodes:
            for dep in node.depends_on:
                if dep not in names:
                    errors.append(f"节点 {node.name!r} 依赖的 {dep!r} 不在 DAG 中")

        # ---- DFS 环检测 ----
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
    # $引用校验
    # ------------------------------------------------------------------

    @staticmethod
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

    @staticmethod
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
