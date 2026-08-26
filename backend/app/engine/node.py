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

#: Signature for a route router: receives context, returns a branch label.
RouterFunc = Callable[[dict[str, Any]], Any]

#: Signature for a human-review approver: receives ``(node_name, payload)``
#: and returns a decision dict: ``{"approve": bool, "reason": Optional[str]}``.
ApproverFunc = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RouteConfig:
    """边级路由配置 —— 源节点声明 ``routes`` 后由声明层构建。

    路由在源节点成功后求值一次：router(ctx) 命中某条支路即激活它，
    其余支路的成员由合成门卫逐个判为 SKIPPED（不执行、不出输出）。
    router 必须是 ctx 的纯函数：支路门卫与执行器各自独立求值，
    resume 后凭恢复的上下文重算也能得到同一标签。
    """

    router: RouterFunc
    branches: dict[str, tuple[str, ...]]  # 标签 -> 支路成员节点名
    default: str | None = None


class RouteError(ValueError):
    """路由结果未命中任何支路且无可用 default —— 路由歧义是错误，不猜测。"""


def resolve_route(route: RouteConfig, ctx: dict[str, Any]) -> str:
    """求值路由标签：router(ctx) 命中支路即返回；未命中回落 default。

    仍未命中抛 :exc:`RouteError`（router 自身抛出的异常原样穿透）。
    """
    label = route.router(ctx)
    if isinstance(label, str) and label in route.branches:
        return label
    if route.default is not None and route.default in route.branches:
        return route.default
    raise RouteError(
        f"路由结果 {label!r} 不在支路 {sorted(route.branches)} 中，且未声明可用的 default"
    )


def route_gate(route: RouteConfig, label: str) -> ConditionFunc:
    """合成支路门卫：标签命中才执行，未命中 SKIPPED。

    路由失败在此兜底为不执行（执行器已在源节点处把失败显式化，
    门卫只在源成功后求值，正常不会走到这个分支）。
    """
    def gate(ctx: dict[str, Any]) -> bool:
        try:
            return resolve_route(route, ctx) == label
        except RouteError:
            return False
    return gate


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
        routes: Optional edge-routing config; the source node resolves the
                branch label after succeeding, members get synthesized gates.
        retry: Retry policy, or ``None`` for no retries.
        timeout: Per-node timeout in seconds, or ``None`` for no limit.
        metadata: Arbitrary user-defined key-value pairs.
    """

    name: str
    func: NodeFunc
    depends_on: list[str] = field(default_factory=list)
    condition: ConditionFunc | None = None
    routes: RouteConfig | None = None
    retry: RetryPolicy | None = None
    timeout: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        deps = ",".join(self.depends_on) if self.depends_on else "root"
        extras = []
        if self.condition:
            extras.append("cond")
        if self.routes:
            extras.append("routes")
        if self.retry:
            extras.append(f"retry={self.retry.max_retries}")
        if self.timeout:
            extras.append(f"timeout={self.timeout}s")
        tag = f", {', '.join(extras)}" if extras else ""
        return f"Node({self.name!r}, deps=[{deps}]{tag})"
