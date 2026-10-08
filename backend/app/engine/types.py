"""
Core types for the DAG Flow orchestration engine.
"""

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


def resolve_ref(ctx: dict[str, Any], value: Any) -> Any:
    """递归解析 value 中的 $ 引用（$a.b.c → ctx["a"]["b"]["c"]）。"""
    if isinstance(value, dict):
        return {k: resolve_ref(ctx, v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_ref(ctx, v) for v in value]
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    parts = value.lstrip("$").split(".")
    val: Any = ctx
    for part in parts:
        if not isinstance(val, dict) or part not in val:
            raise KeyError(f"$ 引用解析失败：{value!r}，无法取 {part!r}")
        val = val[part]
    return val
