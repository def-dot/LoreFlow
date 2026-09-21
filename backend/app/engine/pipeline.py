"""Pipeline — JSON dict → pydantic 对象 + 运行时执行。

- ``nodes`` 是 ``list[Node]``（pydantic 校验，未知字段拒绝）
- start 节点的 inputs 保持原始 dict，由 ``validate_nodes`` 统一校验
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
from .validator import PipeLineValidator

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
    """``__start__`` 节点里单个输入参数的声明（required / label / …）。"""

    model_config = {"extra": "forbid"}

    required: bool | None = None
    default: Any = None
    label: str | None = None
    description: str | None = None
    type: ParamType = ParamType.TEXT
    options: list[str] | None = None  # type=select 时的选项列表


class RetryPolicy(BaseModel):
    """可配置的重试策略（指数退避 + 抖动）。"""

    max_retries: int = 0
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
    timeout: float | None = None

    @field_validator("depends_on", mode="before")
    @classmethod
    def _coerce_depends_on(cls, v: Any) -> Any:
        """depends_on str → list，null / 缺失 → []。"""
        if isinstance(v, str):
            return [v]
        return v if v is not None else []

    @model_validator(mode="after")
    def _coerce_start_inputs(self) -> Node:
        """start 节点的 inputs 值从 raw dict 反序列化为 InputParamDef。"""
        if self.type == "start" and self.inputs:
            self.inputs = {
                k: InputParamDef.model_validate(v) if isinstance(v, dict) else v
                for k, v in self.inputs.items()
            }
        return self

    def resolve_input_schema(self) -> type[BaseModel] | None:
        """返回本节点的输入 schema（来自 REGISTRY）。"""
        func_def = REGISTRY.get(self.type)
        return func_def.input_schema if func_def else None

    def resolve_output_schema(self) -> type[BaseModel] | None:
        """返回本节点的输出 schema。

        优先用 REGISTRY 中的 ``output_schema``；
        没有则从 ``self.inputs`` 的 key 动态生成（start 等声明式节点）；
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

    ``__start__`` 节点的 ``inputs`` 会在校验阶段自动转成 ``dict[str, InputParamDef]``。
    YAML 加载由 ``services/pipelines.py`` 负责，本类只认 dict。
    """

    name: str
    description: str | None = None
    nodes: list[Node]

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_pipeline(self) -> Pipeline:
        """构造期校验"""
        errors = PipeLineValidator.validate(self.nodes)
        if errors:
            raise ValueError("\n".join(errors))
        return self

    @property
    def start_node(self) -> Node:
        """start 节点"""
        return next((n for n in self.nodes if n.type == "start"), None)
    
    @property
    def end_node(self) -> Node:
        """end 节点"""
        return next((n for n in self.nodes if n.type == "end"), None)

    @property
    def inputs(self) -> dict[str, InputParamDef]:
        """start 节点的参数声明（``InputParamDef`` 字典）。"""
        return dict(self.start_node.inputs) if self.start_node and self.start_node.inputs else {}

    @property
    def required_inputs(self) -> list[str]:
        """必填参数键列表。"""
        return [k for k, v in self.inputs.items() if v.required]

    @property
    def default_inputs(self) -> dict[str, Any]:
        """有默认值的参数键 → 默认值。"""
        return {k: v.default for k, v in self.inputs.items() if v.default is not None}

    async def run(
        self,
        inputs: dict[str, Any] | None = None,
        *,
        on_event: Callable[[NodeResult], Awaitable[None]] | None = None,
        approver: ApproverFunc | None = None,
        concurrency: int | None = None,
        resume: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, NodeResult], dict[str, Any] | None]:
        """Execute the DAG.

        Returns:
            ``(results, output)`` — 节点结果映射 + __end__ 输出（若有）。
        """
        from .executor import PipeLineExecutor

        # ---- start 节点：按 InputParamDef 合并默认值 + 校验必填 ----
        inputs = inputs or {}
        if missing := set(self.required_inputs) - set(inputs):
            raise ValueError(f"必填参数缺失: {missing}")
        if extra := set(inputs) - set(self.inputs):
            raise ValueError(f"多余的参数: {extra}")
        inputs = {**self.default_inputs, **inputs}

        if approver is not None:
            inputs["_approver"] = approver

        executor = PipeLineExecutor(
            nodes=self.nodes, ctx=inputs,
            concurrency=concurrency, on_event=on_event,
        )
        results = await executor.execute(resume=resume)

        output = None
        if self.end_node and results.get(self.end_node.name):
            output = results[self.end_node.name].output

        return results, output

    # ---- Mermaid 可视化 ----

    def to_mermaid(self) -> str:
        nodes = {n.name: n for n in self.nodes}
        lines = ["graph TD"]
        for name, node in nodes.items():
            nid = re.sub(r"[ \-]", "_", name)
            func_def = REGISTRY.get(node.type)
            main_text = node.label or name
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


def terminal_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"approve": True}
