"""
Node definitions for DAG Flow.
"""

from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from .types import RetryPolicy

#: Signature for a node's async function: receives the shared context dict, returns anything.
NodeFunc = Callable[..., Coroutine[Any, Any, Any]]

#: Signature for a condition predicate: receives context, returns whether to run.
ConditionFunc = Callable[[dict[str, Any]], bool]

#: Signature for a human-review approver: receives ``(node_name, payload)``
#: and returns a decision dict: ``{"approve": bool, "reason": Optional[str]}``.
ApproverFunc = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class HumanRejected(Exception):
    """Raised by a human review node when the reviewer rejects the payload.

    Carries the rejection details as ``output``; the executor special-cases
    it (终局决策，不进重试循环) and records them in the FAILED node result.
    """

    def __init__(self, reason: str, output: Any = None):
        super().__init__(reason)
        self.output = output


def replay_review_edits(ctx: dict[str, Any], output: Any) -> None:
    """恢复已完成的人工审核节点时，重放审核修订对共享上下文的写回。

    审核通过时 review_func 把修订写回 ctx —— 但 resume 路径不重跑已完成
    节点，ctx 从原始 inputs + 快照重建，多级审核的后续节点（终审视图、
    发布）会退回看到修订前的值。这里按与执行时相同的规则（键须已在决策
    payload 中、非 ``_`` 前缀保留键）重放该副作用。非审核决策形状的
    output 不动。
    """
    if not isinstance(output, dict):
        return
    decision = output.get("decision")
    payload = output.get("payload")
    edits = decision.get("edits") if isinstance(decision, dict) else None
    if not isinstance(edits, dict) or not isinstance(payload, dict):
        return
    for key, value in edits.items():
        if key in payload and not key.startswith("_"):
            ctx[key] = value


@dataclass
class Node:
    """A single executable node in the DAG.

    Each node wraps an async function and declares its upstream dependencies.
    The executor runs nodes as soon as all dependencies have completed.

    Attributes:
        name: Unique identifier for this node within the DAG.
        func: Async callable that receives the shared context dict.
        depends_on: Names of upstream nodes that must complete first.
        condition: Optional predicate; if it returns False the node is skipped.
        retry: Retry policy, or ``None`` for no retries.
        timeout: Per-node timeout in seconds, or ``None`` for no limit.
        metadata: Arbitrary user-defined key-value pairs.
    """

    name: str
    func: NodeFunc
    depends_on: list[str] = field(default_factory=list)
    condition: ConditionFunc | None = None
    retry: RetryPolicy | None = None
    timeout: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        deps = ",".join(self.depends_on) if self.depends_on else "root"
        extras = []
        if self.condition:
            extras.append("cond")
        if self.retry:
            extras.append(f"retry={self.retry.max_retries}")
        if self.timeout:
            extras.append(f"timeout={self.timeout}s")
        tag = f", {', '.join(extras)}" if extras else ""
        return f"Node({self.name!r}, deps=[{deps}]{tag})"
