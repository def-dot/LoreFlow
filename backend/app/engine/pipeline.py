"""Pipeline — JSON dict → pydantic 对象 + 运行时执行。

- ``nodes`` 是 ``list[Node]``（pydantic 校验，未知字段拒绝）
- start 节点的 inputs 保持原始 dict，由 ``validate_nodes`` 统一校验
- ``run()`` / ``validate()`` / ``to_mermaid()`` 直接查 REGISTRY 取函数 / output_schema
- YAML 加载由调用方（services/pipelines.py）负责，Pipeline 只认 dict
"""

from __future__ import annotations

import logging
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.registry import REGISTRY
from .node import ApproverFunc
from .types import NodeResult
from . import validator
from .validator import validate_nodes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic 子模型
# ---------------------------------------------------------------------------

class InputParamDef(BaseModel):
    """``__start__`` 节点里单个输入参数的声明（required / label / …）。"""

    model_config = {"extra": "forbid"}

    required: bool | None = None
    default: Any = None
    label: str | None = None
    description: str | None = None
    multiline: bool | None = None
    file: bool | None = None


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


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


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

    @property
    def node_map(self) -> dict[str, Node]:
        """节点名 → Node 对象。"""
        return {node.name: node for node in self.nodes}

    @model_validator(mode="after")
    def _validate_pipeline(self) -> Pipeline:
        """构造期校验"""
        errors = validate_nodes(self.nodes)
        if errors:
            raise ValueError("\n".join(errors))
        return self

    def _find_node_by_type(self, node_type: str) -> Node | None:
        for node in self.nodes:
            if node.type == node_type:
                return node
        return None

    @property
    def _declared_params(self) -> dict[str, dict[str, Any]]:
        """参数声明 — 没有 ``__start__`` 则为空（= 自由上下文）。"""
        start = self._find_node_by_type("start")
        if start is not None and start.inputs:
            return start.inputs
        return {}

    # ---- 运行时校验（REGISTRY）----

    def validate(self) -> list[str]:
        """结构 + REGISTRY 校验。"""
        nodes = self.node_map
        errors = validator.validate_inputs({}, self._declared_params)
        errors.extend(validator.validate_pipeline(self.name or "", nodes))
        return errors

    # ---- 执行 ----

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
        from .executor import DAGExecutor

        nodes = self.node_map
        declared = self._declared_params

        # ---- input validation（未声明参数 = 自由上下文，不做白名单）----
        if declared:
            validation_errors = validate_inputs(inputs or {}, declared)
            if validation_errors:
                raise ValueError("输入校验失败: " + "; ".join(validation_errors))

        # ---- materialise defaults ----
        merged = {k: v["default"] for k, v in declared.items() if v.get("default") is not None}
        if inputs:
            merged.update(inputs)
        if approver is not None:
            merged["_approver"] = approver

        # start 参数同时以 $start.key 路径可用
        start_node = self._find_node_by_type("start")
        if start_node is not None:
            merged[start_node.name] = {k: v for k, v in merged.items() if not k.startswith("_")}

        executor = DAGExecutor(
            nodes=nodes, ctx=merged,
            concurrency=concurrency, on_event=on_event,
        )
        results = await executor.execute(resume=resume)

        output = None
        end_result = results.get("__end__")
        if end_result is not None and end_result.output:
            output = end_result.output

        return results, output

    # ---- Mermaid 可视化 ----

    def to_mermaid(self) -> str:
        nodes = self.node_map
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
