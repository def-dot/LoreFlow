"""
Node definitions for DAG Flow.
"""

from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.registry import FuncDef
from .types import RetryPolicy

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


def make_func_def(func: Callable, name: str = "", **kw: Any) -> FuncDef:
    """为普通 callable 包一层 FuncDef（内部 / 测试节点用）。"""
    return FuncDef(name=name or getattr(func, "__name__", ""), func=func, **kw)


class HumanRejected(Exception):
    """Raised by a human review node when the reviewer rejects the payload.

    Carries the rejection details as ``output``; the executor special-cases
    it (终局决策，不进重试循环) and records them in the FAILED node result.
    """

    def __init__(self, reason: str, output: Any = None):
        super().__init__(reason)
        self.output = output


@dataclass
class Node:
    """A single executable node in the DAG.
    """

    func_def: FuncDef
    name: str = ""
    label: str = ""
    description: str | None = None
    inputs: dict[str, Any] | None = None
    depends_on: list[str] = field(default_factory=list)
    condition: str | bool | None = None
    retry: RetryPolicy | None = None
    timeout: float | None = None

    @property
    def func(self) -> NodeFunc:
        return self.func_def.func

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
        return f"Node({self.name!r}, label={self.label!r}, deps=[{deps}]{tag})"
