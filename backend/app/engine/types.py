"""
Core types for the DAG Flow orchestration engine.
"""

import contextvars
from collections.abc import Awaitable, Callable, Coroutine, Mapping
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
    inputs: dict[str, Any] | None = None

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
        if self.inputs:
            d["inputs"] = self.inputs
        return d

    def __repr__(self) -> str:
        if self.status == NodeStatus.COMPLETED:
            return f"NodeResult({self.node_name!r}, OK, {self.attempts} attempt(s), {self.duration_ms:.0f}ms)"
        if self.status == NodeStatus.FAILED:
            return f"NodeResult({self.node_name!r}, FAILED, {type(self.error).__name__}: {self.error})"
        return f"NodeResult({self.node_name!r}, {self.status.value})"


#: Signature for a node event listener: receives the finished NodeResult, returns nothing.
NodeEventFunc = Callable[[NodeResult], Awaitable[None]]


class PipeLineExecutionError(Exception):
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


# ---------------------------------------------------------------------------
# 节点函数签名 & 工具
# ---------------------------------------------------------------------------

#: Signature for a node's async function: receives the shared context dict, returns anything.
NodeFunc = Callable[..., Coroutine[Any, Any, Any]]

#: Signature for a condition predicate: receives context, returns whether to run.
ConditionFunc = Callable[[dict[str, Any]], bool]

#: Signature for a human-review approver: receives ``(node_name, payload, labels)``
#: and returns a decision dict: ``{"approve": bool, "reason": Optional[str]}``.
#: ``labels`` maps payload keys to display names (e.g. ``{"title": "标题"}``).
ApproverFunc = Callable[[str, dict[str, Any], dict[str, str]], Awaitable[dict[str, Any]]]


def wired_ctx(ctx: Mapping[str, Any], wiring: Mapping[str, Any] | None) -> dict[str, Any]:
    """接线 → 解析后的 inputs：递归解析 wiring 中的 $ 引用，仅返回 wiring 本身。"""

    def _deref(ref: str) -> Any:
        val: Any = ctx
        for part in ref[1:].split("."):
            if not isinstance(val, Mapping) or part not in val:
                raise KeyError(f"$ 引用解析失败：{ref}，无法取 {part}）")
            val = val[part]
        return val

    def _resolve(obj: Any) -> Any:
        if isinstance(obj, str) and obj.startswith("$"):
            return _deref(obj)
        if isinstance(obj, dict):
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(v) for v in obj]
        return obj

    if not wiring:
        return {}
    return {k: _resolve(v) for k, v in wiring.items()}


class NodeContext:
    """引擎注入的节点运行上下文（与用户 inputs 分离）。"""

    __slots__ = ("node_name", "stored_decision")

    def __init__(self, node_name: str, stored_decision: dict[str, Any] | None = None):
        self.node_name = node_name
        self.stored_decision = stored_decision


#: NodeContext 注入：executor 在调用节点函数前设置，函数内通过 .get() 读取。
current_node_ctx: contextvars.ContextVar[NodeContext] = contextvars.ContextVar("current_node_ctx")


class HumanRejected(Exception):
    """Raised by a human review node when the reviewer rejects the payload.

    Carries the rejection details as ``output``; the executor special-cases
    it (终局决策，不进重试循环) and records them in the FAILED node result.
    """

    def __init__(self, reason: str, output: Any = None):
        super().__init__(reason)
        self.output = output
