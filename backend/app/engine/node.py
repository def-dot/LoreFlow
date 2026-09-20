"""
Node definitions for DAG Flow.
"""

from collections.abc import Awaitable, Callable, Coroutine, Mapping
from typing import Any

#: Signature for a node's async function: receives the shared context dict, returns anything.
NodeFunc = Callable[..., Coroutine[Any, Any, Any]]

#: Signature for a condition predicate: receives context, returns whether to run.
ConditionFunc = Callable[[dict[str, Any]], bool]

#: Signature for a human-review approver: receives ``(node_name, payload)``
#: and returns a decision dict: ``{"approve": bool, "reason": Optional[str]}``.
ApproverFunc = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


def wired_ctx(ctx: Mapping[str, Any], wiring: Mapping[str, Any] | None) -> dict[str, Any]:
    """接线 → 节点上下文 ``{**ctx, **{本地键: 解析值}}``（唯一实现，全引擎共用）。
    """
    def resolve(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("$"):
            value: Any = ctx
            for part in v[1:].split("."):
                if not isinstance(value, Mapping) or part not in value:
                    return None
                value = value[part]
            return value
        return v

    if not wiring:
        return dict(ctx)
    return {**ctx, **{k: resolve(v) for k, v in wiring.items()}}


class HumanRejected(Exception):
    """Raised by a human review node when the reviewer rejects the payload.

    Carries the rejection details as ``output``; the executor special-cases
    it (终局决策，不进重试循环) and records them in the FAILED node result.
    """

    def __init__(self, reason: str, output: Any = None):
        super().__init__(reason)
        self.output = output
