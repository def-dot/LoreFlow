"""
Core types for the DAG Flow orchestration engine.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class NodeStatus(Enum):
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
    CANCELLED = "cancelled"


@dataclass
class NodeResult:
    """The result of executing a single DAG node.

    Attributes:
        node_name: The node's name.
        status: Final execution status.
        output: The return value of the node function (if completed).
        error: The exception that caused failure (if failed).
        attempts: Number of execution attempts (1 + retries).
        duration_ms: Wall-clock duration of the final attempt in milliseconds.
        retry_history: 重试记录列表（由 executor 构建，每次 RETRYING 追加）。
    """

    node_name: str
    status: NodeStatus
    output: Any = None
    error: Exception | None = None
    attempts: int = 0
    duration_ms: float = 0.0
    retry_history: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (the shape the web UI consumes)."""
        d: dict[str, Any] = {
            "status": self.status.value,
            "output": self.output,
            "attempts": self.attempts,
        }
        if self.error:
            d["error"] = str(self.error)
        if self.duration_ms:
            d["duration_ms"] = round(self.duration_ms)
        if self.retry_history:
            d["attempts_log"] = self.retry_history
        return d

    def __repr__(self) -> str:
        if self.status == NodeStatus.COMPLETED:
            return f"NodeResult({self.node_name!r}, OK, {self.attempts} attempt(s), {self.duration_ms:.0f}ms)"
        if self.status == NodeStatus.FAILED:
            return f"NodeResult({self.node_name!r}, FAILED, {type(self.error).__name__}: {self.error})"
        return f"NodeResult({self.node_name!r}, {self.status.value})"


#: Signature for a node event listener: receives the finished NodeResult, returns nothing.
NodeEventFunc = Callable[[NodeResult], Awaitable[None]]


class DAGExecutionError(Exception):
    """Raised when the DAG execution fails (one or more nodes failed)."""

    def __init__(self, message: str, results: dict[str, NodeResult]):
        super().__init__(message)
        self.results = results


class SuspendExecution(BaseException):
    """内部控制流信号：人工审批节点挂起，run 干净退出等待 /approve。
    """

    def __init__(self, message: str, results: dict[str, Any]):
        super().__init__(message)
        self.results = results
