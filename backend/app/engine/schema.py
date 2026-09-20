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
            if spec.type not in REGISTRY:
                errors.append(f"节点 {name!r}: 类型函数 {spec.type!r} 未注册")
                continue

            errors.extend(self._validate_inputs(name, spec))
            errors.extend(self._validate_condition(name, spec))

        if errors:
            raise ValueError("DAG 配置无效:\n  " + "\n  ".join(errors))

        return self

    # ---- 接线参数校验 ----

    def _validate_inputs(self, name: str, spec: NodeSpec) -> list[str]:
        """$引用上游存在性 + 字段存在性 + required。"""
        loc = f"节点 {name!r}"
        errors: list[str] = []
        func_def = REGISTRY[spec.type]
        inputs = spec.inputs or {}
        input_schema = func_def.input_schema.model_fields if func_def.input_schema else {}

        for key, finfo in input_schema.items():
            if finfo.is_required() and key not in inputs:
                errors.append(f"{loc}: inputs 缺少必填参数 {key!r}")

        upstream = self.get_upstream_nodes(name)
        for key, value in inputs.items():
            if isinstance(value, str) and value.startswith("$"):
                errors.extend(f"{loc}: inputs.{key}: {msg}" for msg in self._check_ref(value, upstream))

        return errors

    # ---- condition 校验 ----

    def _validate_condition(self, name: str, spec: NodeSpec) -> list[str]:
        loc = f"节点 {name!r}"
        condition = spec.condition
        if not condition:
            return []
        if isinstance(condition, bool):
            return []
        if not isinstance(condition, str) or not condition.strip():
            return [f"{loc}: condition 必须是非空表达式字符串，实际是 {condition!r}"]
        try:
            refs = condition_keys(condition)
        except ValueError as exc:
            return [f"{loc}: {exc}"]
        upstream = self.get_upstream_nodes(name)
        errors: list[str] = []
        for ref in refs:
            errors.extend(f"{loc}: {msg}" for msg in self._check_ref(f"${ref}", upstream))
        return errors

    # ---- $引用校验 ----

    def _check_ref(self, ref: str, upstream: set[str]) -> list[str]:
        """校验单个 $引用：上游节点存在性 + 字段存在性。"""
        root, _, field = ref[1:].partition(".")
        if root not in upstream:
            return [f"引用的 {root!r} 不是上游依赖节点"]
        if not field:
            return []
        up_spec = self.nodes.get(root)
        up_func = REGISTRY.get(up_spec.type) if up_spec else None
        top_field = field.split('.')[0]
        if up_func and up_func.output_schema and top_field not in up_func.output_schema.model_fields:
            return [f"引用的 {root!r} 输出中没有字段 {top_field!r}"]
        return []

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
        """返回 name 的所有上游节点（传递闭包）。"""
        seen: set[str] = set()
        stack = [name]
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

