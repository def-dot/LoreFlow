"""
Core types for the DAG Flow orchestration engine.
"""

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class NodeStatus(StrEnum):
    """Execution status of a DAG node."""

    PENDING = "pending"
    RUNNING = "running"
    REVIEWING = "reviewing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    UPSTREAM_FAILED = "upstream_failed"
    SKIPPED = "skipped"
    UPSTREAM_SKIPPED = "upstream_skipped"
    UPSTREAM_REVIEWING = "upstream_reviewing"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (NodeStatus.PENDING, NodeStatus.RUNNING, NodeStatus.RETRYING, NodeStatus.UPSTREAM_REVIEWING)


class NodeResult(BaseModel):
    """The result of executing a single DAG node."""

    node_name: str
    status: NodeStatus
    output: Any = None
    error: str | None = None
    attempts: int = 0
    duration_ms: float = 0.0
    retry_history: list[dict[str, Any]] | None = None
    inputs: dict[str, Any] | None = None


class NodeSchema(BaseModel):
    """节点实例的 input/output schema。"""

    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class SuspendExecution(BaseException):
    """内部控制流信号：人工审批节点挂起，run 干净退出等待 /approve。"""


# ---------------------------------------------------------------------------
# 节点函数签名 & 工具
# ---------------------------------------------------------------------------


REF_RE = re.compile(r"\$([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*)")


def resolve_ref(
    ctx: dict[str, Any],
    value: Any,
    skipped: set[str] | None = None,
) -> Any:
    """递归解析 value 中的 $ 引用。

    - 纯引用 ``$a.b.c`` → 返回原始值（保持类型）。
    - 内嵌引用 ``"前置 $a.b 后缀"`` → 字符串替换。
    - 引用指向跳过节点 → 纯引用返回 ``None``，内嵌引用替换为空字符串。
    """
    if isinstance(value, dict):
        return {k: resolve_ref(ctx, v, skipped) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_ref(ctx, v, skipped) for v in value]
    if not isinstance(value, str):
        return value

    def _is_skipped(ref: str) -> bool:
        return skipped is not None and ref.split(".")[0] in skipped

    def _deref(ref: str) -> Any:
        parts = ref.split(".")
        v: Any = ctx
        for part in parts:
            if not isinstance(v, dict) or part not in v:
                raise KeyError(f"$ 引用解析失败：${ref!r}，无法取 {part!r}")
            v = v[part]
        return v

    # 纯引用：保持原始类型
    m = REF_RE.fullmatch(value)
    if m:
        return None if _is_skipped(m.group(1)) else _deref(m.group(1))
    # 内嵌引用：字符串替换
    return REF_RE.sub(
        lambda m: "" if _is_skipped(m.group(1)) else str(_deref(m.group(1))), value)
