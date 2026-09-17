"""Pipeline YAML → Pydantic 模型（结构校验 + 语义校验合一）。

PipelineConfig 是 YAML 配置的唯一校验入口：
- 结构校验：类型、必填、字段归一化（Pydantic 自动）
- 语义校验：图结构、$ 引用来源、节点类型注册、参数声明（model_validator）
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.registry import REGISTRY

from .condition import _parse


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
        if not isinstance(v, dict):
            return v
        return {k: (val if isinstance(val, dict) else {"label": str(val)}) for k, val in v.items()}

    # ------------------------------------------------------------------
    # 语义校验（图结构、$ 引用、注册表、参数声明）
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_semantics(self) -> PipelineConfig:
        """跨字段语义校验 — 收集全部错误后一次性抛出。"""
        errors: list[str] = []

        if not self.nodes:
            raise ValueError("流水线至少需要一个节点")

        # 1) 图结构
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

        func_def = REGISTRY[spec.type]
        param_keys = set(self.inputs)
        refs = self._ancestors(name, edges) | param_keys

        # wiring
        if spec.inputs:
            accepts_extra = False
            try:
                accepts_extra = any(
                    p.kind is inspect.Parameter.VAR_KEYWORD
                    for p in inspect.signature(func_def.func).parameters.values()
                )
            except (ValueError, TypeError):
                pass
            errors.extend(
                f"{loc}: {msg}"
                for msg in self._validate_wiring(spec.inputs, refs, func_def.input_schema, param_keys, accepts_extra)
            )
            refs = refs | set(spec.inputs)

        # condition
        if spec.condition:
            errors.extend(f"{loc}: {msg}" for msg in self._validate_condition(spec.condition, refs))

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
        """depends_on 类型 + 依赖存在性 + 环检测。"""
        errors: list[str] = []
        for name, deps in edges.items():
            if not deps:
                continue
            if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
                errors.append(f"节点 {name!r}: depends_on 必须是字符串列表")
                continue
            for dep in deps:
                if dep not in edges:
                    errors.append(f"节点 {name!r} 依赖的 {dep!r} 不在 DAG 中")

        # 环检测
        cycle = PipelineConfig._find_cycle(
            {n: d for n, d in edges.items()
             if isinstance(d, list) and all(isinstance(x, str) for x in d)}
        )
        if cycle:
            errors.append(f"检测到循环依赖: {' → '.join(cycle)}")
        return errors

    # ---- wiring 校验 ----

    @staticmethod
    def _validate_wiring(
        wiring: dict[str, Any],
        available_refs: set[str],
        input_schema: type[BaseModel] | None = None,
        param_keys: set[str] | None = None,
        accepts_extra: bool = False,
    ) -> list[str]:
        """inputs 声明校验：$ 引用根键 ∈ 参数键 ∪ 上游闭包。"""
        errors: list[str] = []
        for local, source in wiring.items():
            if not (isinstance(source, str) and source.startswith("$")):
                continue
            root = source[1:].partition(".")[0]
            if root not in available_refs:
                errors.append(f"inputs.{local} 引用的 {root!r} 不是参数键或上游依赖节点")

        if input_schema is not None:
            schema_keys = set(input_schema.model_fields)
            extra = set(wiring) - schema_keys
            if extra and not accepts_extra:
                errors.append(f"inputs 包含节点未声明的参数: {', '.join(sorted(extra))}")
            if param_keys is not None:
                for key, finfo in input_schema.model_fields.items():
                    if not finfo.is_required():
                        continue
                    if key in wiring or key in param_keys:
                        continue
                    errors.append(f"inputs 缺少必填参数 {key!r}")

        return errors

    # ---- condition 校验 ----

    @staticmethod
    def _validate_condition(condition: Any, available_refs: set[str]) -> list[str]:
        if isinstance(condition, bool):
            return []
        if not isinstance(condition, str) or not condition.strip():
            return [f"condition 必须是非空表达式字符串，实际是 {condition!r}"]
        try:
            root = _parse(condition)[1].split(".")[0]
        except ValueError as exc:
            return [str(exc)]
        if root not in available_refs:
            return [f"condition 引用的 {root!r} 不是参数键、上游依赖节点或本地键"]
        return []

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
    def _ancestors(name: str, edges: Mapping[str, Any]) -> set[str]:
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


# ------------------------------------------------------------------
# 兼容入口 — 旧调用方仍可用
# ------------------------------------------------------------------

def validate_config(config: dict[str, Any]) -> list[str]:
    """校验完整 DAG 配置，返回全部错误（空列表 = 合法）。"""
    try:
        PipelineConfig(**config)
        return []
    except ValidationError as exc:
        errors: list[str] = []
        for err in exc.errors():
            msg = err["msg"].removeprefix("Value error, ")
            loc = err["loc"]
            if len(loc) >= 2 and loc[0] == "nodes" and not msg.startswith("DAG"):
                node_name = loc[1]
                if err["type"] == "missing" and "type" in loc:
                    msg = f"节点 {node_name!r}: 需要 'type'（函数键）"
                else:
                    msg = f"节点 {node_name!r}: {msg}"
            errors.append(msg)
        return errors
    except (ValueError, TypeError) as exc:
        return [str(exc)]
