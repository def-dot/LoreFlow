"""Pipeline YAML → Pydantic 模型（结构校验 + 语义校验合一）。

PipelineConfig 是 YAML 配置的唯一校验入口：
- 结构校验：类型、必填、字段归一化（Pydantic 自动）
- 语义校验：图结构、$ 引用来源、节点类型注册、参数声明（model_validator）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.registry import REGISTRY

from .condition import condition_keys
from app.registry.types import FuncDef


# ------------------------------------------------------------------
# Pipeline YAML 模型
# ------------------------------------------------------------------

class InputSpec(BaseModel):
    """流水线输入声明。"""

    required: bool = False
    file: bool = False
    label: str = ""
    description: str = ""
    multiline: bool = False
    default: Any = None
    validate: str | None = None


class RetrySpec(BaseModel):
    """重试策略声明（解析为 RetryPolicy 由 resolve.parse_retry 负责）。"""

    max_retries: int = 0
    backoff_base: float | None = None
    backoff_factor: float | None = None
    backoff_max: float | None = None
    jitter: bool | None = None
    retry_on: list[str] | None = None


class NodeSpec(BaseModel):
    """DAG 节点声明。"""

    type: str
    label: str = ""
    description: str | None = None
    inputs: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list)
    condition: str | bool | None = None
    retry: int | RetrySpec | None = None
    timeout: float | None = None

    @field_validator("depends_on", mode="before")
    @classmethod
    def _normalize_deps(cls, v: Any) -> list[str]:
        """YAML 允许 depends_on: load（裸字符串），统一为列表。"""
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return v
        raise ValueError(f"depends_on 必须是字符串或列表，实际是 {type(v).__name__}")


class PipelineConfig(BaseModel):
    """完整流水线 YAML 配置 — 唯一校验入口。

    结构校验由 Pydantic 字段定义自动完成；
    语义校验在 __post_init_validator 中统一收集。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    description: str | None = None
    nodes: dict[str, NodeSpec] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # 语义校验（图结构、$ 引用、注册表、参数声明）
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_semantics(self) -> PipelineConfig:
        """跨字段语义校验 — 收集全部错误后一次性抛出。"""
        errors: list[str] = []

        if not self.nodes:
            raise ValueError("流水线至少需要一个节点")

        errors.extend(self._validate_graph())

        for name, spec in self.nodes.items():
            loc = f"节点 {name!r}"

            if spec.type not in REGISTRY:
                errors.append(f"{loc}: 类型函数 {spec.type!r} 未注册")
                continue

            upstream = self.get_upstream_nodes(name)
            refs = upstream | set(spec.inputs or {})
            errors.extend(f"{loc}: {msg}" for msg in self._validate_inputs(spec))
            errors.extend(f"{loc}: {msg}" for msg in self._validate_condition(spec.condition, refs))

        if errors:
            raise ValueError("DAG 配置无效:\n  " + "\n  ".join(errors))

        return self

    # ---- 接线参数校验 ----

    def _validate_inputs(self, spec: NodeSpec) -> list[str]:
        """$引用上游存在性 + 字段存在性 + required。"""
        errors: list[str] = []
        func_def = REGISTRY[spec.type]
        inputs = spec.inputs or {}
        input_schema = func_def.input_schema.model_fields if func_def.input_schema else {}

        # required
        for key, finfo in input_schema.items():
            if finfo.is_required() and key not in inputs:
                errors.append(f"inputs 缺少必填参数 {key!r}")

        # 逐参数 $引用
        upstream = self.get_upstream_nodes(spec.name)
        for key, value in inputs.items():
            finfo = input_schema.get(key)
            if finfo is None:
                continue
            if isinstance(value, str) and value.startswith("$"):
                root, _, field = value[1:].partition(".")
                if root not in upstream:
                    errors.append(f"inputs.{key} 引用的 {root!r} 不是上游依赖节点")
                elif field:
                    up_spec = nodes.get(root)
                    up_func = REGISTRY.get(up_spec.type) if up_spec else None
                    if up_func and up_func.output_schema and field not in up_func.output_schema.model_fields:
                        errors.append(f"inputs.{key} 引用的 {root!r} 输出中没有字段 {field!r}")

        return errors

    # ---- condition 校验 ----

    @staticmethod
    def _validate_condition(condition: Any, refs: set[str]) -> list[str]:
        if not condition:
            return []
        if isinstance(condition, bool):
            return []
        if not isinstance(condition, str) or not condition.strip():
            return [f"condition 必须是非空表达式字符串，实际是 {condition!r}"]
        try:
            roots = condition_keys(condition)
        except ValueError as exc:
            return [str(exc)]
        errors: list[str] = []
        for root in roots:
            if root not in refs:
                errors.append(f"condition 引用的 {root!r} 不是上游依赖节点")
        return errors

    # ---- 图结构校验 ----

    def _validate_graph(self) -> list[str]:
        """依赖存在性 + 环检测。"""
        edges = {name: spec.depends_on for name, spec in self.nodes.items()}
        errors: list[str] = []
        for name, deps in edges.items():
            if not deps:
                continue
            for dep in deps:
                if dep not in edges:
                    errors.append(f"节点 {name!r} 依赖的 {dep!r} 不在 DAG 中")

        cycle = self._find_cycle(edges)
        if cycle:
            errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
        return errors

    # ---- 图工具 ----

    def get_upstream_nodes(self, name: str) -> set[str]:
        """返回 name 的所有上游节点（传递闭包）。__start__ 始终视为隐式上游。"""
        seen: set[str] = set()
        stack = [name]
        if "__start__" in self.nodes and name != "__start__":
            seen.add("__start__")
        while stack:
            for dep in (getattr(self.nodes.get(stack.pop()), "depends_on", None) or []):
                if dep not in seen:
                    seen.add(dep)
                    stack.append(dep)
        return seen

    @staticmethod
    def _find_cycle(edges: Mapping[str, Sequence[str]]) -> list[str] | None:
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
                    return cycle
        return None

