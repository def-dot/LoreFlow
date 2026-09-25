"""
Core types for the DAG Flow orchestration engine.
"""

from collections.abc import Awaitable, Callable, Coroutine, Mapping
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
    CANCELLED = "cancelled"


ACTIVE_STATUSES = (NodeStatus.PENDING, NodeStatus.RUNNING, NodeStatus.RETRYING)


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


#: Signature for a node event listener: receives the finished NodeResult, returns nothing.
NodeEventFunc = Callable[[NodeResult], Awaitable[None]]


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
