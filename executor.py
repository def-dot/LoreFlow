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
from typing import Any, Dict, Optional

from node import Node
from schems import NodeResult, NodeStatus, RetryPolicy

logger = logging.getLogger(__name__)


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

    def __init__(self, concurrency: Optional[int] = None):
        self._semaphore: Optional[asyncio.Semaphore] = (
            asyncio.Semaphore(concurrency) if concurrency else None
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        nodes: Dict[str, Node],
        inputs: Optional[Dict[str, Any]] = None,
        fail_fast: bool = False,
    ) -> Dict[str, NodeResult]:
        """Execute all *nodes* and return a mapping of node name → NodeResult.

        Args:
            nodes: All nodes in the DAG (keyed by name).
            inputs: Initial key-value pairs placed into the shared context.
            fail_fast: If True, cancel all running nodes as soon as any node
                       fails (after retries exhausted).

        Returns:
            Dict mapping each node name to its :class:`NodeResult`.

        Raises:
            DAGExecutionError: If *fail_fast* is False and one or more nodes
                               ultimately failed.
        """
        # ----- shared state -----
        ctx: Dict[str, Any] = dict(inputs) if inputs else {}
        statuses: Dict[str, NodeStatus] = {}
        events: Dict[str, asyncio.Event] = {
            name: asyncio.Event() for name in nodes
        }
        results: Dict[str, NodeResult] = {}

        # Track tasks so we can cancel on fail_fast
        tasks: Dict[str, asyncio.Task[None]] = {}

        async def run_node(node: Node) -> None:
            """Lifecycle of a single node.

            Task-level safety net: however ``_run_one`` exits, record the
            terminal status and always set the event so downstream nodes
            never hang.
            """
            try:
                await self._run_one(node, ctx, statuses, events, results)
            except asyncio.CancelledError:
                statuses[node.name] = NodeStatus.CANCELLED
                results[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.CANCELLED,
                )
            except Exception as exc:
                # Should not happen — _run_one is defensive, but guard anyway
                logger.exception("Unexpected error in executor for %s", node.name)
                statuses[node.name] = NodeStatus.FAILED
                results[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.FAILED,
                    error=exc,
                )
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
                name for name, r in results.items()
                if r.status == NodeStatus.FAILED
            ]
            if failed:
                raise DAGExecutionError(
                    f"DAG execution finished with {len(failed)} failed node(s): "
                    f"{', '.join(failed)}",
                    results,
                )

        return results

    # ------------------------------------------------------------------
    # Single-node execution
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        node: Node,
        ctx: Dict[str, Any],
        statuses: Dict[str, NodeStatus],
        events: Dict[str, asyncio.Event],
        results: Dict[str, NodeResult],
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
            results[node.name] = NodeResult(
                node_name=node.name,
                status=NodeStatus.SKIPPED,
            )
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
                results[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.SKIPPED,
                )
                return

        # ---- 4. Execute with retry ----
        retry = node.retry or RetryPolicy(max_retries=0)
        last_error: Optional[Exception] = None
        statuses[node.name] = NodeStatus.RUNNING

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
                results[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.COMPLETED,
                    output=output,
                    attempts=attempt + 1,
                    duration_ms=duration_ms,
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
                await asyncio.sleep(delay)

        # ---- 5. All retries exhausted ----
        statuses[node.name] = NodeStatus.FAILED
        results[node.name] = NodeResult(
            node_name=node.name,
            status=NodeStatus.FAILED,
            error=last_error,
            attempts=retry.max_retries + 1,
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
