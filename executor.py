"""
Async DAG executor — the core execution engine.

Uses an "all-tasks-upfront, event-driven" model:
1. Every node is spawned as an asyncio Task immediately.
2. Each task waits on asyncio.Event objects for its upstream dependencies.
3. When a node finishes it sets its own event, unblocking downstream nodes.
4. Nodes without dependencies start right away; independent nodes run concurrently.

This naturally respects the DAG topology without a centralized scheduler.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Coroutine, Dict, Optional

from node import Node
from schems import NodeResult, NodeStatus, RetryPolicy

logger = logging.getLogger(__name__)


#: Reserved context key under which the executor exposes the current
#: :class:`Execution` to node functions (used by human review nodes).
_EXECUTION_KEY = "__execution__"


class Execution(Awaitable[Dict[str, NodeResult]]):
    """Live state of a single DAG run; await it for the final results dict.

    The executor updates :attr:`nodes` as nodes transition, and records
    reviews requested by human nodes in :attr:`pending` (answer them with
    :meth:`resolve_review`). Failures land in :attr:`error` instead of
    raising — only cancellation propagates, flipping :attr:`cancelled`.
    """

    def __init__(self) -> None:
        self._coro: Optional[Coroutine[Any, Any, Dict[str, NodeResult]]] = None
        self._task: Optional[asyncio.Task] = None
        self.nodes: Dict[str, NodeResult] = {}
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.error: Optional[str] = None
        self.cancelled: bool = False
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None

    def start(self) -> asyncio.Task:
        """Begin execution in a background task and return it.

        Alternatively, ``await`` the execution directly (runs inline in
        the awaiter's task).
        """
        if self._task is None:
            self._task = asyncio.create_task(self._coro)
        return self._task

    def __await__(self):
        if self._task is not None:
            return self._task.__await__()
        return self._coro.__await__()

    @property
    def status(self) -> str:
        """One of pending / running / completed / failed / cancelled."""
        if self.cancelled:
            return "cancelled"
        if self.error:
            return "failed"
        if self.finished_at:
            return "completed"
        if self.started_at:
            return "running"
        return "pending"

    # ------------------------------------------------------------------
    # Human review plumbing (internal — see DAG.human_node)
    # ------------------------------------------------------------------

    def request_review(
        self,
        node_name: str,
        payload: Dict[str, Any],
        approver: Optional[Callable] = None,
    ) -> asyncio.Future:
        """Register a pending review and return the future that resolves
        with the decision. If *approver* is given it answers the review
        automatically (e.g. ``terminal_approver`` for CLI runs)."""
        future = asyncio.get_running_loop().create_future()
        task = None
        if approver is not None:
            task = asyncio.create_task(
                self._auto_approve(approver, node_name, payload, future)
            )
        self.pending[node_name] = {"payload": payload, "future": future, "task": task}
        return future

    async def _auto_approve(
        self, approver: Callable, node_name: str, payload: Dict[str, Any],
        future: asyncio.Future,
    ) -> None:
        try:
            decision = await approver(node_name, payload)
        except Exception as exc:  # approver crashed — fail the review
            if not future.done():
                future.set_exception(exc)
            return
        if not future.done():
            future.set_result(decision)

    def resolve_review(self, node_name: str, decision: Dict[str, Any]) -> bool:
        """Answer a pending review; *decision* looks like
        ``{"approve": bool, "reason": Optional[str]}``.

        Returns ``False`` if no review for *node_name* was pending.
        """
        entry = self._drop_review(node_name)
        if entry is None:
            return False
        if not entry["future"].done():
            entry["future"].set_result(decision)
        return True

    def _drop_review(self, node_name: str) -> Optional[Dict[str, Any]]:
        """Remove a pending review, cancelling its auto-approver (if any)."""
        entry = self.pending.pop(node_name, None)
        if entry is not None and entry["task"] is not None and not entry["task"].done():
            entry["task"].cancel()
        return entry


class DAGExecutionError(Exception):
    """Raised when the DAG execution fails (one or more nodes failed)."""

    def __init__(self, message: str, results: Dict[str, NodeResult]):
        super().__init__(message)
        self.results = results


class DAGExecutor:
    """Executes a DAG concurrently, respecting node dependencies.

    Attributes:
        concurrency: Maximum number of nodes to run simultaneously.
                     ``None`` means unlimited.
    """

    def __init__(
        self,
        concurrency: Optional[int] = None,
        on_event: Optional[Callable[[NodeResult], None]] = None,
    ):
        self._semaphore: Optional[asyncio.Semaphore] = (
            asyncio.Semaphore(concurrency) if concurrency else None
        )
        self.on_event = on_event

    def _emit(self, node_name: str, status: NodeStatus, **fields: Any) -> None:
        """Push a node state change to the ``on_event`` callback (if set)."""
        if self.on_event is not None:
            self.on_event(NodeResult(node_name=node_name, status=status, **fields))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        nodes: Dict[str, Node],
        inputs: Optional[Dict[str, Any]] = None,
        fail_fast: bool = False,
        execution: Optional[Execution] = None,
    ) -> Dict[str, NodeResult]:
        """Execute all *nodes* and return a mapping of node name → NodeResult.

        Args:
            nodes: All nodes in the DAG (keyed by name).
            inputs: Initial key-value pairs placed into the shared context.
            fail_fast: If True, cancel all running nodes as soon as any node
                       fails (after retries exhausted).
            execution: The :class:`Execution` receiving live state (a fresh
                       one is created if not given). Loop bodies reuse the
                       parent's so their results and reviews stay visible.

        Returns:
            Dict mapping each node name to its :class:`NodeResult`.

        Raises:
            DAGExecutionError: If *fail_fast* is False and one or more nodes
                               ultimately failed.
        """
        # ----- shared state -----
        ctx: Dict[str, Any] = dict(inputs) if inputs else {}
        if execution is None:
            inherited = ctx.get(_EXECUTION_KEY)
            execution = inherited if isinstance(inherited, Execution) else Execution()
        ctx[_EXECUTION_KEY] = execution
        statuses: Dict[str, NodeStatus] = {}
        events: Dict[str, asyncio.Event] = {
            name: asyncio.Event() for name in nodes
        }

        # Track tasks so we can cancel on fail_fast
        tasks: Dict[str, asyncio.Task[None]] = {}

        async def run_node(node: Node) -> None:
            """Lifecycle of a single node.

            Task-level safety net: however ``_run_one`` exits, record the
            terminal status and always set the event so downstream nodes
            never hang.
            """
            try:
                await self._run_one(node, ctx, statuses, events, execution)
            except asyncio.CancelledError:
                statuses[node.name] = NodeStatus.CANCELLED
                execution.nodes[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.CANCELLED,
                )
                self._emit(node.name, NodeStatus.CANCELLED)
            except Exception as exc:
                # Should not happen — _run_one is defensive, but guard anyway
                logger.exception("Unexpected error in executor for %s", node.name)
                statuses[node.name] = NodeStatus.FAILED
                execution.nodes[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.FAILED,
                    error=exc,
                )
                self._emit(node.name, NodeStatus.FAILED, error=exc)
            finally:
                events[node.name].set()

        # ----- spawn all nodes -----
        for node in nodes.values():
            tasks[node.name] = asyncio.create_task(run_node(node))

        # ----- wait for completion -----
        await asyncio.gather(*tasks.values(), return_exceptions=True)

        # ----- optionally fail-fast on first error -----
        if not fail_fast:
            failed = [
                name for name, r in execution.nodes.items()
                if r.status == NodeStatus.FAILED
            ]
            if failed:
                raise DAGExecutionError(
                    f"DAG execution finished with {len(failed)} failed node(s): "
                    f"{', '.join(failed)}",
                    execution.nodes,
                )

        return execution.nodes

    # ------------------------------------------------------------------
    # Single-node execution
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        node: Node,
        ctx: Dict[str, Any],
        statuses: Dict[str, NodeStatus],
        events: Dict[str, asyncio.Event],
        execution: Execution,
    ) -> None:
        """Run a single node through its full lifecycle."""

        # ---- 1. Wait for dependencies ----
        for dep in node.depends_on:
            await events[dep].wait()

        # ---- 2. Check for cascading failure ----
        failed_deps = [
            dep for dep in node.depends_on
            if statuses.get(dep) == NodeStatus.FAILED
        ]
        if failed_deps:
            logger.warning(
                "[%s] Skipped - upstream failed: %s", node.name, failed_deps
            )
            statuses[node.name] = NodeStatus.SKIPPED
            execution.nodes[node.name] = NodeResult(
                node_name=node.name,
                status=NodeStatus.SKIPPED,
            )
            self._emit(node.name, NodeStatus.SKIPPED)
            return

        # ---- 3. Evaluate condition (branching) ----
        if node.condition is not None:
            try:
                should_run = node.condition(ctx)
            except Exception as exc:
                logger.error(
                    "[%s] Condition raised %s: %s - skipping node",
                    node.name, type(exc).__name__, exc,
                )
                should_run = False

            if not should_run:
                logger.info("[%s] Skipped - condition not met", node.name)
                statuses[node.name] = NodeStatus.SKIPPED
                execution.nodes[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.SKIPPED,
                )
                self._emit(node.name, NodeStatus.SKIPPED)
                return

        # ---- 4. Execute with retry ----
        retry = node.retry or RetryPolicy(max_retries=0)
        last_error: Optional[Exception] = None
        statuses[node.name] = NodeStatus.RUNNING
        self._emit(node.name, NodeStatus.RUNNING)

        for attempt in range(retry.max_retries + 1):
            try:
                start = time.monotonic()

                if self._semaphore:
                    async with self._semaphore:
                        output = await self._call(node, ctx)
                else:
                    output = await self._call(node, ctx)

                duration_ms = (time.monotonic() - start) * 1000

                # success
                ctx[node.name] = output
                statuses[node.name] = NodeStatus.COMPLETED
                execution.nodes[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.COMPLETED,
                    output=output,
                    attempts=attempt + 1,
                    duration_ms=duration_ms,
                )
                self._emit(
                    node.name, NodeStatus.COMPLETED,
                    output=output, attempts=attempt + 1, duration_ms=duration_ms,
                )
                logger.info(
                    "[%s] OK  completed  (attempt %d/%d, %.0f ms)",
                    node.name,
                    attempt + 1,
                    retry.max_retries + 1,
                    duration_ms,
                )
                return

            except Exception as exc:
                last_error = exc

                if not retry.should_retry(exc, attempt):
                    logger.error(
                        "[%s] FAIL  non-retryable / retries exhausted: %s: %s",
                        node.name, type(exc).__name__, exc,
                    )
                    break

                delay = retry.get_delay(attempt)
                logger.warning(
                    "[%s] RETRY  attempt %d/%d failed (%s: %s), "
                    "retrying in %.1f s ...",
                    node.name,
                    attempt + 1,
                    retry.max_retries + 1,
                    type(exc).__name__,
                    exc,
                    delay,
                )
                self._emit(node.name, NodeStatus.RETRYING, attempts=attempt + 1, error=exc)
                await asyncio.sleep(delay)

        # ---- 5. All retries exhausted ----
        statuses[node.name] = NodeStatus.FAILED
        execution.nodes[node.name] = NodeResult(
            node_name=node.name,
            status=NodeStatus.FAILED,
            error=last_error,
            attempts=retry.max_retries + 1,
        )
        self._emit(
            node.name, NodeStatus.FAILED,
            error=last_error, attempts=retry.max_retries + 1,
        )
        logger.error(
            "[%s] FAILED after %d attempt(s): %s: %s",
            node.name,
            retry.max_retries + 1,
            type(last_error).__name__ if last_error else "?",
            last_error,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _call(node: Node, ctx: Dict[str, Any]) -> Any:
        """Invoke *node.func* with timeout if configured."""
        coro = node.func(ctx)
        if node.timeout is not None:
            return await asyncio.wait_for(coro, timeout=node.timeout)
        return await coro
