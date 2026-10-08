"""校验层（构造期，pydantic 校验之后）。

``PipelineValidator(pipeline).validate()`` — 图结构 + 引用校验。

字段级校验（type 注册、timeout>0、condition 非空白与语法、inputs schema）
由 pydantic 在 Node model 校验期完成。
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from .condition import parse_condition

if TYPE_CHECKING:
    from .pipeline import Pipeline


class PipelineValidator:
    """Pipeline 构造期校验：图结构 + 引用合法性。"""

    def __init__(self, pipeline: "Pipeline"):
        self.pipeline = pipeline
        self.errors: list[str] = []

    def validate(self) -> list[str]:
        self._validate_dag()
        self._validate_required()
        self._validate_inputs()
        self._validate_condition()
        return self.errors

    # ---- 工具方法 ----

    def _upstreams(self, node_name: str) -> set[str]:
        """返回指定节点的所有上游节点（depends_on 的传递闭包）。"""
        nodes = {n.name: n for n in self.pipeline.nodes}
        node = nodes[node_name]
        seen: set[str] = set()
        stack = list(node.depends_on)
        while stack:
            dep = stack.pop()
            if dep not in seen:
                seen.add(dep)
                stack.extend(
                    getattr(nodes.get(dep), "depends_on", None) or []
                )
        return seen

    # ---- 图结构校验 ----

    def _validate_dag(self) -> None:
        """节点名去重、依赖存在性、DFS 环检测。"""
        nodes = self.pipeline.nodes
        names = [n.name for n in nodes]

        # 节点名去重
        dupes = sorted(name for name, cnt in Counter(names).items() if cnt > 1)
        if dupes:
            self.errors.append(f"节点名重复: {', '.join(dupes)}")

        name_set = set(names)

        # 依赖存在性
        for node in nodes:
            for dep in node.depends_on:
                if dep not in name_set:
                    self.errors.append(f"节点 {node.name!r} 依赖的 {dep!r} 不在 DAG 中")

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
                    self.errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
                    break

    # ---- params 校验 ----

    def _validate_required(self) -> None:
        """required 中的键必须存在于 params。"""
        if not self.pipeline.required:
            return
        unknown = set(self.pipeline.required) - set(self.pipeline.params or {})
        if unknown:
            self.errors.append(f"required 引用了不存在的参数: {unknown}")

    # ---- inputs 校验 ----

    def _validate_inputs(self) -> None:
        for node in self.pipeline.nodes:
            if not node.inputs:
                continue
            upstream = self._upstreams(node.name)
            for key, value in node.inputs.items():
                errs: list[str] = []
                if self.pipeline.get_value_schema(value, errs, upstream) is None:
                    for e in errs:
                        self.errors.append(f"节点 {node.name!r}: 参数 {key!r} {e}")

    # ---- condition 校验 ----

    def _validate_condition(self) -> None:
        for node in self.pipeline.nodes:
            if node.condition is None or isinstance(node.condition, bool):
                continue

            upstream = self._upstreams(node.name)
            groups = parse_condition(node.condition)
            for and_group in groups:
                for _, key, _, _ in and_group:
                    ref = f"${key}"

                    # 上游 + 路径有效性
                    errs: list[str] = []
                    if self.pipeline.get_value_schema(ref, errs, upstream) is None:
                        for e in errs:
                            self.errors.append(f"节点 {node.name!r}: condition {e}")
