"""Pipeline — JSON dict → pydantic 对象 + 运行时执行。

- ``nodes`` 是 ``list[Node]``（pydantic 校验，未知字段拒绝）
- ``params`` 是 Pipeline 级输入参数声明（JSON Schema properties），与节点 inputs（数据接线）分离
- ``required`` 是必填参数名列表（JSON Schema required）
- 所有节点的 inputs 统一保持 dict（数据接线）；
- ``run()`` / ``validate()`` / ``to_mermaid()`` 直接查 REGISTRY 取函数 / output_schema
- YAML 加载由调用方（services/pipelines.py）负责，Pipeline 只认 dict
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

_JSON_SCHEMA_TYPES = frozenset({
    "string", "number", "integer", "boolean", "array", "object", "null",
})
_UI_WIDGETS = frozenset({
    "text", "textarea", "select", "checkbox", "file", "file_list", "number",
})
# type → 允许的 ui 组合（None 表示不写 ui 时的默认值）
_TYPE_UI_COMPAT: dict[str, frozenset[str | None]] = {
    "string":  frozenset({None, "text", "textarea", "select"}),
    "number":  frozenset({None, "number"}),
    "integer": frozenset({None, "number"}),
    "array":   frozenset({None, "checkbox", "select", "file_list"}),
    "object":  frozenset({None, "file"}),
}

from app.registry import REGISTRY
from .types import NodeResult, NodeSchema
from .validator import PipelineValidator

logger = logging.getLogger(__name__)

# Python 类型名 → JSON Schema type
_INFER_SCHEMA: dict[str, dict[str, str]] = {
    "str": {"type": "string"},
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "bool": {"type": "boolean"},
    "list": {"type": "array"},
    "dict": {"type": "object"},
}


class ParamSchema(BaseModel):
    """单个参数的 JSON Schema 声明。

    type 必填；常用字段显式声明；其余字段（如 ui等）自由扩展。
    """

    model_config = ConfigDict(extra="allow")

    type: str | list[str]
    title: str | None = None

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str | list[str]) -> str | list[str]:
        types = [v] if isinstance(v, str) else v
        for t in types:
            if t not in _JSON_SCHEMA_TYPES:
                raise ValueError(f"非法的 JSON Schema 类型 {t!r}，可选: {sorted(_JSON_SCHEMA_TYPES)}")
        return v
    description: str | None = None
    default: Any = None
    enum: list[Any] | None = None
    items: dict[str, Any] | None = None       # array 的元素 schema
    properties: dict[str, Any] | None = None   # object 的属性 schema
    ui: str | None = None                     # UI 控件：textarea / select / checkbox / file / file_list

    @field_validator("ui")
    @classmethod
    def _validate_ui(cls, v: str | None) -> str | None:
        if v is not None and v not in _UI_WIDGETS:
            raise ValueError(f"非法的 ui 控件 {v!r}，可选: {sorted(_UI_WIDGETS)}")
        return v

    @model_validator(mode="after")
    def _check_type_ui_compat(self) -> "ParamSchema":
        t = self.type if isinstance(self.type, str) else self.type[0] if self.type else None
        allowed = _TYPE_UI_COMPAT.get(t)
        if allowed is not None and self.ui not in allowed:
            raise ValueError(f"type={t!r} 不支持 ui={self.ui!r}，可选: {sorted(allowed - {None}) or '不写 ui'}")
        return self


class RetryPolicy(BaseModel):
    """可配置的重试策略（指数退避 + 抖动）。"""

    max_retries: int = Field(default=0, ge=0)
    backoff_base: float = 1.0
    backoff_factor: float = 2.0
    backoff_max: float = 60.0
    retry_on: tuple[type[Exception], ...] = (Exception,)
    jitter: bool = True

    @model_serializer
    def _serialize(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "backoff_base": self.backoff_base,
            "backoff_factor": self.backoff_factor,
            "backoff_max": self.backoff_max,
            "retry_on": [e.__name__ for e in self.retry_on],
            "jitter": self.jitter,
        }

    def get_delay(self, attempt: int) -> float:
        """Compute the backoff delay for a given retry attempt (0-indexed)."""
        delay = self.backoff_base * (self.backoff_factor**attempt)
        delay = min(delay, self.backoff_max)
        if self.jitter:
            delay *= 0.5 + random.random()
        return delay

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """Return True if the exception is retryable and attempts remain."""
        if attempt >= self.max_retries:
            return False
        return isinstance(exception, self.retry_on)


class Node(BaseModel):
    """DAG 中一个可执行节点（纯声明层）。

    运行时需要函数 / output_schema 时，直接查 ``REGISTRY[node.type]``。
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    label: str = ""
    description: str | None = None
    inputs: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list)
    condition: str | bool | None = None
    retry: int | RetryPolicy | None = None
    timeout: float | None = Field(default=None, gt=0)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _coerce_depends_on(cls, v: Any) -> Any:
        """depends_on str → list，null / 缺失 → []。"""
        if isinstance(v, str):
            return [v]
        return v if v is not None else []

    @field_validator("retry", mode="before")
    @classmethod
    def _coerce_retry(cls, v: Any) -> Any:
        """retry dict → RetryPolicy（int/RetryPolicy/None 透传）。"""
        if isinstance(v, dict):
            from app.engine.resolve import parse_retry
            return parse_retry(v)
        return v

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        """type 必须已在 REGISTRY 注册。"""
        if v not in REGISTRY:
            raise ValueError(f"未知的 type {v!r}")
        return v

    @field_validator("condition")
    @classmethod
    def _validate_condition(cls, v: str | bool | None) -> str | bool | None:
        """condition 字符串非空白 + 语法合法（类型约束由注解 ``str | bool | None`` 保证）。"""
        if isinstance(v, str):
            if not v.strip():
                raise ValueError("condition 不能为空字符串")
            from .condition import parse_condition
            parse_condition(v)  # 语法错误会抛 ValueError
        return v

    @model_validator(mode="after")
    def _validate_and_coerce_inputs(self) -> Node:
        """inputs 校验（必填 + 未知参数检查）。"""
        if self.inputs is None:
            return self

        func_def = REGISTRY.get(self.type)
        if func_def is None:
            return self

        schema = func_def.input_schema
        if schema is None:
            return self

        required = schema.get("required") or []
        known = set(schema.get("properties") or {})
        allow_extra = bool(schema.get("additionalProperties", True))

        msgs = []
        missing = [k for k in required if k not in self.inputs]
        if missing:
            msgs.append(f"缺少必填参数 {missing}")
        if not allow_extra:
            unknown = set(self.inputs) - known
            if unknown:
                msgs.append(f"包含未知参数 {unknown}")
        if msgs:
            raise ValueError("inputs " + ", ".join(msgs))
        return self

    def __repr__(self) -> str:
        deps = ",".join(self.depends_on) if self.depends_on else "root"
        extras = []
        if self.condition:
            extras.append("cond")
        if isinstance(self.retry, RetryPolicy):
            extras.append(f"retry={self.retry.max_retries}")
        elif isinstance(self.retry, int):
            extras.append(f"retry={self.retry}")
        if self.timeout:
            extras.append(f"timeout={self.timeout}s")
        tag = f", {', '.join(extras)}" if extras else ""
        return f"Node({self.name!r}, type={self.type!r}, deps=[{deps}]{tag})"


class PipelineMetadata(BaseModel):
    """Pipeline 级元数据（YAML metadata 段）。"""
    agent_tool: bool = True  # false 时不注册为 Agent 工具


class Pipeline(BaseModel):
    """JSON dict → pydantic 对象 + 运行时执行。

    用法::

        p = Pipeline.model_validate(data)
        results, _ = await p.run(inputs={"q": "hello"})

    ``params`` 是 Pipeline 级输入参数声明（JSON Schema properties）；
    YAML 加载由 ``services/pipelines.py`` 负责，本类只认 dict。
    """

    name: str
    description: str | None = None
    params: dict[str, ParamSchema] | None = None  # JSON Schema properties（type 必填）
    required: list[str] | None = None              # JSON Schema required
    nodes: list[Node]
    metadata: PipelineMetadata = Field(default_factory=PipelineMetadata)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_pipeline(self) -> Pipeline:
        """构造期校验（图结构 + $引用 + required 一致性）。"""
        errors = PipelineValidator(self).validate()
        if errors:
            raise ValueError("\n".join(errors))
        return self

    def validate_inputs(self, inputs: dict[str, Any] | None) -> None:
        """运行时校验用户输入：必填 / 多余参数。"""
        inputs = inputs or {}
        required = self.required or []
        if missing := set(required) - set(inputs):
            raise ValueError(f"必填参数缺失: {missing}")
        if extra := set(inputs) - set(self.params or {}):
            raise ValueError(f"多余的参数: {extra}")

    @property
    def end_node(self) -> Node:
        """end 节点"""
        return next((n for n in self.nodes if n.type == "end"), None)

    async def run(
        self,
        inputs: dict[str, Any] | None = None,
        *,
        on_event: Callable[[NodeResult], Awaitable[None]] | None = None,
        concurrency: int | None = None,
        resume: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, NodeResult], dict[str, Any] | None]:
        """Execute the DAG.

        Returns:
            ``(results, output)`` — 节点结果映射 + __end__ 输出（若有）。
        """
        from .executor import PipeLineExecutor

        executor = PipeLineExecutor(
            nodes=self.nodes, ctx={"params": inputs or {}},
            concurrency=concurrency, on_event=on_event,
            resume=resume,
        )
        results = await executor.execute()

        output = (
            r.output.model_dump()
            if self.end_node and (r := results.get(self.end_node.name)) and r.output
            else None
        )

        return results, output

    # ---- Schema 推导 ----

    def get_value_schema(self, value: Any) -> dict[str, Any] | None:
        """返回值的 JSON Schema（$引用解析路径，字面量推断类型）。"""
        if not isinstance(value, str) or not value.startswith("$"):
            return _INFER_SCHEMA.get(type(value).__name__, {"type": "string"})
        parts = value.lstrip("$").split(".")
        root, segments = parts[0], parts[1:]

        if root == "params":
            if not self.params:
                return None
            root_schema: dict[str, Any] | None = {
                "type": "object",
                "properties": {k: v.model_dump() for k, v in self.params.items()},
            }
        else:
            nodes_dict = {n.name: n for n in self.nodes}
            node = nodes_dict.get(root)
            if not node:
                return None
            root_schema = self.get_node_schema(node).output_schema
            if not root_schema:
                return None

        if not segments:
            return root_schema

        schema: dict[str, Any] = root_schema
        for seg in segments:
            props = schema.get("properties")
            if isinstance(props, dict) and seg in props:
                schema = props[seg]
            else:
                return None
        return schema

    def get_node_schema(self, node: Node) -> NodeSchema:
        """获取节点实例的 input/output schema（per-instance，处理动态类型）。"""
        func_def = REGISTRY.get(node.type)
        inp = func_def.input_schema if func_def else None
        out = func_def.output_schema if func_def else None

        # additionalProperties=true 的节点（end / human / code 等）：合并动态参数
        need_dynamic = (inp and inp.get("additionalProperties")) or (out and out.get("additionalProperties"))
        if node.inputs and need_dynamic:
            dynamic = {k: s for k, ref in node.inputs.items() if (s := self.get_value_schema(ref))}
            if dynamic:
                if inp and inp.get("additionalProperties"):
                    inp = {**inp, "properties": {**dynamic, **(inp.get("properties") or {})}}
                if out and out.get("additionalProperties"):
                    out = {**out, "properties": {**dynamic, **(out.get("properties") or {})}}

        return NodeSchema(input_schema=inp, output_schema=out)

    # ---- Mermaid 可视化 ----

    # Mermaid 保留字，用作节点 ID 时加 _ 前缀避免解析错误
    _MERMAID_RESERVED = frozenset({
        "end", "subgraph", "graph", "flowchart", "class", "click", "link",
        "style", "default", "direction", "interpolate", "fill", "stroke",
    })

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for node in self.nodes:
            nid = re.sub(r"[ \-]", "_", node.name)
            if nid in self._MERMAID_RESERVED:
                nid = f"_{nid}"
            func_def = REGISTRY.get(node.type)
            main_text = node.label or node.name
            small: list[str] = []
            if func_def and func_def.label:
                small.append(func_def.label)
            if node.condition:
                small.append("[?]")
            rp = node.retry
            if isinstance(rp, int):
                small.append(f"[R{rp}]")
            elif isinstance(rp, RetryPolicy) and rp.max_retries:
                small.append(f"[R{rp.max_retries}]")
            text = main_text + (f"<br/><i>{' '.join(small)}</i>" if small else "")
            lines.append(f'    {nid}["{text}"]')
            for dep in node.depends_on:
                did = re.sub(r"[ \-]", "_", dep)
                if did in self._MERMAID_RESERVED:
                    did = f"_{did}"
                lines.append(f"    {did} --> {nid}")
        return "\n".join(lines)
