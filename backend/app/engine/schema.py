"""Pipeline YAML → Pydantic 模型（结构校验 + 语义校验合一）。

PipelineConfig 是 YAML 配置的唯一校验入口：
- 结构校验：类型、必填、字段归一化（Pydantic 自动）
- 语义校验：图结构、$ 引用来源、节点类型注册、参数声明（model_validator）
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator, model_validator

from app.registry import REGISTRY
from app.registry.types import FuncDef

from .condition import condition_keys


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

    name: str = ""
    description: str | None = None
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    nodes: dict[str, NodeSpec] = Field(default_factory=dict)

    @field_validator("inputs", mode="before")
    @classmethod
    def _parse_inputs(cls, v: Any) -> dict[str, Any]:
        """YAML inputs 值若是纯字符串，包装为 InputSpec(label=...)。"""
        if v is None:
            return {}
        if not isinstance(v, dict):
            return v
        return {k: (val if isinstance(val, dict) else {"label": val}) for k, val in v.items()}

    # ------------------------------------------------------------------
    # 语义校验（图结构、$ 引用、注册表、参数声明）
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_semantics(self) -> PipelineConfig:
        """跨字段语义校验 — 收集全部错误后一次性抛出。"""
        errors: list[str] = []

        if not self.nodes:
            raise ValueError("流水线至少需要一个节点")

        # 1) 图结构（依赖的节点在不在图中；有没有环）
        edges = {name: spec.depends_on for name, spec in self.nodes.items()}
        errors.extend(self._validate_graph(edges))

        # 2) 每个节点的语义
        for name, spec in self.nodes.items():
            errors.extend(self._validate_node(name, spec, edges))

        # 3) inputs 参数声明
        errors.extend(self._validate_inputs_decl())

        # 4) output 引用
        errors.extend(self._validate_output())

        if errors:
            raise ValueError("DAG 配置无效:\n  " + "\n  ".join(errors))

        return self

    # ---- 每节点校验 ----

    def _validate_node(self, name: str, spec: NodeSpec, edges: dict[str, list[str]]) -> list[str]:
        errors: list[str] = []
        loc = f"节点 {name!r}"

        # 类型是否注册
        if spec.type not in REGISTRY:
            errors.append(f"{loc}: 类型函数 {spec.type!r} 未注册")
            return errors  # 未注册则后续校验无意义

        upstream = self.get_upstream_nodes(name, edges)
        errors.extend(f"{loc}: {msg}" for msg in self._validate_inputs(spec, upstream))

        if spec.condition:
            errors.extend(f"{loc}: {msg}" for msg in self._validate_condition(spec, upstream))

        refs = refs | set(spec.inputs or {})

        # _review 卡片
        if spec.type == "human" and isinstance(spec.inputs, dict) and spec.inputs.get("_review") is not None:
            errors.extend(
                f"{loc}: {msg}" for msg in self._validate_review(spec.inputs["_review"], refs)
            )

        # code 节点必须有 script
        if spec.type == "code":
            script = (spec.inputs or {}).get("script")
            if not script or not isinstance(script, str) or not script.strip():
                errors.append(f"{loc}: code 类型必须在 inputs 中提供非空 script")

        return errors

    # ---- inputs 参数声明校验 ----

    def _validate_inputs_decl(self) -> list[str]:
        errors: list[str] = []
        clash = sorted(set(self.inputs) & set(self.nodes))
        if clash:
            errors.append(f"输入参数键与节点名冲突: {', '.join(clash)}")

        for name, spec in self.inputs.items():
            for bool_field in ("required", "multiline", "file"):
                value = getattr(spec, bool_field, None)
                if value is not None and not isinstance(value, bool):
                    errors.append(f"参数 {name!r}: {bool_field} 必须是布尔值")
        return errors

    # ---- output 引用校验 ----

    def _validate_output(self) -> list[str]:
        if self.output is None:
            return []
        if not isinstance(self.output, dict):
            return [f"output 必须是映射，实际是 {type(self.output).__name__}"]
        if not self.output:
            return ["output 映射不能为空"]

        known = set(self.nodes) | set(self.inputs)
        errors: list[str] = []
        for key, ref in self.output.items():
            if not isinstance(key, str) or not key:
                errors.append(f"output 映射键必须是非空字符串，实际是 {key!r}")
                continue
            if not isinstance(ref, str) or not ref.startswith("$"):
                errors.append(f"output.{key} 必须是 $ 开头的引用，实际是 {ref!r}")
                continue
            root = ref[1:].split(".")[0]
            if root and root not in known:
                errors.append(f"output.{key} 引用的 {root!r} 不在节点或输入中")
        return errors

    # ---- 图结构校验 ----

    @staticmethod
    def _validate_graph(edges: dict[str, list[str]]) -> list[str]:
        """依赖存在性 + 环检测。"""
        errors: list[str] = []
        for name, deps in edges.items():
            if not deps:
                continue
            for dep in deps:
                if dep not in edges:
                    errors.append(f"节点 {name!r} 依赖的 {dep!r} 不在 DAG 中")

        # 环检测
        cycle = PipelineConfig._find_cycle(edges)
        if cycle:
            errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
        return errors

    # ---- 接线参数校验（$引用 + schema 对照 + required）----

    def _validate_inputs(
        self,
        spec: NodeSpec,
        upstream: set[str],
    ) -> list[str]:
        """节点 inputs 校验"""
        errors: list[str] = []
        func_def = REGISTRY[spec.type]

        if func_def.input_schema is None:
            if not func_def.accepts_extra and spec.inputs:
                errors.append(f"inputs 包含节点未声明的参数: {', '.join(sorted(spec.inputs))}")
            return errors

        # 1) $ 引用根键校验
        for local, source in spec.inputs.items():
            if isinstance(source, str) and source.startswith("$"):
                root = source[1:].partition(".")[0]
                if root not in upstream or root not in self.inputs:
                    errors.append(f"inputs.{local} 引用的 {root!r} 不是参数键或上游依赖节点")

        schema_keys = set(func_def.input_schema.model_fields)

        # 2) 多余参数
        extra = set(spec.inputs) - schema_keys
        if extra and not func_def.accepts_extra:
            errors.append(f"inputs 包含节点未声明的参数: {', '.join(sorted(extra))}")

        # 3) 字面量值类型校验（$ 引用跳过，运行时才知道类型）
        for key, value in spec.inputs.items():
            if isinstance(value, str) and value.startswith("$"):
                continue
            finfo = func_def.input_schema.model_fields.get(key)
            if finfo is None:
                continue
            try:
                TypeAdapter(finfo.annotation).validate_python(value, strict=True)
            except ValidationError:
                errors.append(
                    f"inputs.{key} 类型不匹配，期望 {finfo.annotation}，"
                    f"实际 {type(value).__name__}: {value!r}"
                )

        # 4) required 参数必须在显式接线中提供
        for key, finfo in func_def.input_schema.model_fields.items():
            if finfo.is_required() and key not in spec.inputs:
                errors.append(f"inputs 缺少必填参数 {key!r}")

        return errors

    # ---- condition 校验 ----

    def _validate_condition(self,
        spec: NodeSpec,
        upstream: set[str]
    ) -> list[str]:
        if isinstance(spec, bool):
            return []
        if not isinstance(spec, str) or not spec.strip():
            return [f"condition 必须是非空表达式字符串，实际是 {spec!r}"]
        try:
            roots = condition_keys(spec)
        except ValueError as exc:
            return [str(exc)]
        errors: list[str] = []
        for root in roots:
            if root not in upstream:
                errors.append(f"condition 引用的 {root!r} 不是参数键、上游依赖节点或本地键")
        return errors

    # ---- _review 卡片校验 ----

    @staticmethod
    def _validate_review(review: Any, available_refs: set[str]) -> list[str]:
        if not isinstance(review, dict):
            return [f"_review 必须是映射，实际是 {type(review).__name__}"]
        if not review:
            return ["_review 声明不能为空映射"]

        errors: list[str] = []
        for k in review:
            if not (isinstance(k, str) and k.startswith("$") and len(k) > 1):
                errors.append(f"_review 键 {k!r} 必须带 $ 引用前缀")
                continue
            root = k[1:].split(".")[0]
            if root in ("approve", "reason"):
                errors.append(f"_review 字段 {root!r} 不得占用协议键")
            if root not in available_refs:
                errors.append(f"_review 字段 {root!r} 不是参数键、上游依赖节点或本地键")
        return errors

    # ---- 图工具 ----

    @staticmethod
    def get_upstream_nodes(name: str, edges: Mapping[str, Any]) -> set[str]:
        """返回 name 的所有上游节点（传递闭包）。"""
        seen: set[str] = set()
        stack = [name]
        while stack:
            for dep in edges.get(stack.pop()) or []:
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
