"""
Core types for the DAG Flow orchestration engine.
"""

from collections.abc import Mapping
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


class SuspendExecution(BaseException):
    """内部控制流信号：人工审批节点挂起，run 干净退出等待 /approve。"""


# ---------------------------------------------------------------------------
# 节点函数签名 & 工具
# ---------------------------------------------------------------------------


def deref(ctx: Mapping[str, Any], ref: str) -> Any:
    """按点路径从 ctx 取值（``$a.b.c`` → ``ctx["a"]["b"]["c"]``）。缺键抛 ``KeyError``。"""
    val: Any = ctx
    for part in ref.lstrip("$").split("."):
        if not isinstance(val, Mapping) or part not in val:
            raise KeyError(f"$ 引用解析失败：{ref}，无法取 {part}）")
        val = val[part]
    return val


def wired_ctx(ctx: Mapping[str, Any], wiring: Mapping[str, Any] | None) -> dict[str, Any]:
    """接线 → 解析后的 inputs：递归解析 wiring 中的 $ 引用，仅返回 wiring 本身。"""

    def _resolve(obj: Any) -> Any:
        if isinstance(obj, str) and obj.startswith("$"):
            return deref(ctx, obj)
        if isinstance(obj, dict):
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(v) for v in obj]
        return obj

    if not wiring:
        return {}
    return {k: _resolve(v) for k, v in wiring.items()}
