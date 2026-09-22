"""Pipeline — JSON dict → pydantic 对象 + 运行时执行。

- ``nodes`` 是 ``list[Node]``（pydantic 校验，未知字段拒绝）
- ``params`` 是 Pipeline 级输入参数声明（InputParamDef），与节点 inputs（数据接线）分离
- 所有节点的 inputs 统一保持 dict（数据接线）；
- ``run()`` / ``validate()`` / ``to_mermaid()`` 直接查 REGISTRY 取函数 / output_schema
- YAML 加载由调用方（services/pipelines.py）负责，Pipeline 只认 dict
"""

from __future__ import annotations

import enum
import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.registry import REGISTRY
from .types import ApproverFunc
from .types import NodeResult
from .validator import validate_dag, validate_ref

logger = logging.getLogger(__name__)


class ParamType(str, enum.Enum):
    """输入参数类型（类似 Dify）。"""

    TEXT = "text"            # 单行文本
    PARAGRAPH = "paragraph"  # 多行文本（textarea）
    NUMBER = "number"        # 数字
    SELECT = "select"        # 下拉选项
    CHECKBOX = "checkbox"    # 复选框
    FILE = "file"            # 单文件
    FILE_LIST = "file_list"  # 文件列表


class InputParamDef(BaseModel):
    """Pipeline 级单个输入参数的声明（required / label / …）。"""

    model_config = {"extra": "forbid"}

    required: bool | None = None
    default: Any = None
    label: str | None = None
    description: str | None = None
    type: ParamType = ParamType.TEXT
    options: list[str] | None = None  # type=select 时的选项列表


def validate_and_merge(
    params: dict[str, InputParamDef],
    inputs: dict[str, Any] | None,
) -> dict[str, Any]:
    """校验必填 / 多余参数，合并默认值，返回最终输入。"""
    inputs = inputs or {}
    if missing := {k for k, v in params.items() if v.required} - set(inputs):
        raise ValueError(f"必填参数缺失: {missing}")
    if extra := set(inputs) - set(params):
        raise ValueError(f"多余的参数: {extra}")
    defaults = {k: v.default for k, v in params.items() if v.default is not None}
    return {**defaults, **inputs}


class RetryPolicy(BaseModel):
    """可配置的重试策略（指数退避 + 抖动）。"""

    max_retries: int = Field(default=0, ge=0)
    backoff_base: float = 1.0
    backoff_factor: float = 2.0
    backoff_max: float = 60.0
    retry_on: tuple[type[Exception], ...] = (Exception,)
    jitter: bool = True

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
        """inputs 校验（必填 + 未知参数检查，$引用不做类型校验）。"""
        if self.inputs is None:
            return self

        schema = self.resolve_input_schema()
        if schema is None:
            return self

        fields = schema.model_fields
        missing = [k for k, f in fields.items() if f.is_required() and k not in self.inputs]
        unknown = set(self.inputs) - set(fields)
        msgs = []
        if missing:
            msgs.append(f"缺少必填参数 {missing}")
        if unknown:
            msgs.append(f"包含未知参数 {unknown}")
        if msgs:
            raise ValueError("inputs " + ", ".join(msgs))
        return self

    def resolve_input_schema(self) -> type[BaseModel] | None:
        """返回本节点的输入 schema（来自 REGISTRY）。"""
        func_def = REGISTRY.get(self.type)
        return func_def.input_schema if func_def else None

    def resolve_output_schema(self) -> type[BaseModel] | None:
        """返回本节点的输出 schema。

        优先用 REGISTRY 中的 ``output_schema``；
        没有则从 ``self.inputs`` 的 key 动态生成；
        都没有返回 ``None``（不校验字段）。
        """
        from pydantic import create_model

        func_def = REGISTRY.get(self.type)
        if func_def and func_def.output_schema is not None:
            return func_def.output_schema
        if self.inputs:
            fields: dict[str, Any] = {k: (Any, None) for k in self.inputs}
            return create_model(f"DynamicOutput_{self.type}", **fields)
        return None

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


class Pipeline(BaseModel):
    """JSON dict → pydantic 对象 + 运行时执行。

    用法::

        p = Pipeline(**data)
        results, _ = await p.run(inputs={"q": "hello"})

    ``params`` 是 Pipeline 级输入参数声明（InputParamDef）；
    YAML 加载由 ``services/pipelines.py`` 负责，本类只认 dict。
    """

    name: str
    description: str | None = None
    params: dict[str, InputParamDef] | None = None
    nodes: list[Node]

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_pipeline(self) -> Pipeline:
        """构造期校验（图结构 + $引用）。"""
        errors = validate_dag(self.nodes)
        errors.extend(validate_ref(self.nodes, param_keys=set(self.params) if self.params else None))
        if errors:
            raise ValueError("\n".join(errors))
        return self

    @property
    def end_node(self) -> Node:
        """end 节点"""
        return next((n for n in self.nodes if n.type == "end"), None)

    @property
    def required_inputs(self) -> list[str]:
        """必填参数键列表。"""
        return [k for k, v in (self.params or {}).items() if v.required]

    @property
    def default_inputs(self) -> dict[str, Any]:
        """有默认值的参数键 → 默认值。"""
        return {k: v.default for k, v in (self.params or {}).items() if v.default is not None}

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

        # ---- 按 params 合并默认值 + 校验必填 ----
        merged = validate_and_merge(self.params or {}, inputs)

        executor = PipeLineExecutor(
            nodes=self.nodes, ctx={"input": merged},
            concurrency=concurrency, on_event=on_event,
        )
        results = await executor.execute(resume=resume)

        output = None
        if self.end_node and results.get(self.end_node.name):
            output = results[self.end_node.name].output

        return results, output

    # ---- Mermaid 可视化 ----

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for node in self.nodes:
            nid = re.sub(r"[ \-]", "_", node.name)
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
                lines.append(f"    {did} --> {nid}")
        return "\n".join(lines)


def terminal_approver(node_name: str, payload: dict[str, Any], labels: dict[str, str] | None = None) -> dict[str, Any]:
    return {"approve": True}
