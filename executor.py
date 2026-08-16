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
from typing import Any, Callable, Dict, Optional

from node import Node
from schems import DAGExecutionError, NodeResult, NodeStatus, RetryPolicy

logger = logging.getLogger(__name__)


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

    def _emit(self, result: NodeResult) -> None:
        """Push a node state change to the ``on_event`` callback (if set)."""
        if self.on_event is not None:
            self.on_event(result)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        nodes: Dict[str, Node],
        inputs: Optional[Dict[str, Any]] = None,
        resume: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, NodeResult]:
        """Execute all *nodes* and return a mapping of node name → NodeResult.

        Args:
            nodes: All nodes in the DAG (keyed by name).
            inputs: Initial key-value pairs placed into the shared context.
            resume: 重启恢复用的节点快照 ``{name: {status, output, ...}}``；
                    已完成节点不重跑，其输出作为下游上下文；其余节点正常
                    重跑（含重新挂起的审批节点）。

        Returns:
            Dict mapping each node name to its :class:`NodeResult`.

        Raises:
            DAGExecutionError: If one or more nodes ultimately failed.
        """
        # ----- shared state -----
        ctx: Dict[str, Any] = dict(inputs) if inputs else {}
        statuses: Dict[str, NodeStatus] = {}
        events: Dict[str, asyncio.Event] = {
            name: asyncio.Event() for name in nodes
        }
        results: Dict[str, NodeResult] = {}

        # ----- resume: 已完成节点直接置为完成，输出进上下文 -----
        resume = resume or {}
        for name, saved in resume.items():
            if saved.get("status") == "completed":
                ctx[name] = saved.get("output")
                statuses[name] = NodeStatus.COMPLETED
                events[name].set()
                results[name] = NodeResult(
                    node_name=name,
                    status=NodeStatus.COMPLETED,
                    output=saved.get("output"),
                    attempts=saved.get("attempts") or 1,
                    duration_ms=saved.get("duration_ms") or 0.0,
                )

        # Task registry — gathered at the end
        tasks: Dict[str, asyncio.Task[None]] = {}

        async def run_node(node: Node) -> None:
            """Lifecycle of a single node: 等依赖 → 执行 → 收尾。

            Task-level safety net: however ``_run_one`` exits, ``results``
            carries the terminal result. 状态机(statuses)/同步(events)/事件
            汇报三件事都在这里收口——全部从 results 这一个真相推导。
            """
            try:
                for dep in node.depends_on:
                    await events[dep].wait()
                results[node.name] = await self._run_one(node, ctx, statuses)
            except asyncio.CancelledError:
                results[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.CANCELLED,
                )
            except Exception as exc:
                # Should not happen — _run_one is defensive, but guard anyway
                logger.exception("Unexpected error in executor for %s", node.name)
                results[node.name] = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.FAILED,
                    error=exc,
                )
            finally:
                statuses[node.name] = results[node.name].status
                events[node.name].set()
                self._emit(results[node.name])

        # ----- spawn remaining nodes -----
        for node in nodes.values():
            if node.name in resume and resume[node.name].get("status") == "completed":
                continue  # 恢复时已完成，不重跑
            tasks[node.name] = asyncio.create_task(run_node(node))

        # ----- wait for completion -----
        await asyncio.gather(*tasks.values(), return_exceptions=True)

        # ----- surface failures as DAGExecutionError -----
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
    ) -> NodeResult:
        """Run a single node (依赖已在 ``run_node`` 中等完) and return its
        terminal result: 级联跳过检查 → 条件判断 → 带重试的执行。
        结果存储与终态汇报由 ``run_node`` 负责，这里只生产。"""

        # ---- 1. Check for cascading failure ----
        failed_deps = [
            dep for dep in node.depends_on
            if statuses.get(dep) == NodeStatus.FAILED
        ]
        if failed_deps:
            logger.warning(
                "[%s] Skipped - upstream failed: %s", node.name, failed_deps
            )
            return NodeResult(
                node_name=node.name,
                status=NodeStatus.SKIPPED,
            )

        # ---- 2. Evaluate condition (branching) ----
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
                return NodeResult(
                    node_name=node.name,
                    status=NodeStatus.SKIPPED,
                )

        # ---- 3. Execute with retry ----
        retry = node.retry or RetryPolicy(max_retries=0)
        last_error: Optional[Exception] = None
        self._emit(NodeResult(node_name=node.name, status=NodeStatus.RUNNING))

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
                logger.info(
                    "[%s] OK  completed  (attempt %d/%d, %.0f ms)",
                    node.name,
                    attempt + 1,
                    retry.max_retries + 1,
                    duration_ms,
                )
                return NodeResult(
                    node_name=node.name,
                    status=NodeStatus.COMPLETED,
                    output=output,
                    attempts=attempt + 1,
                    duration_ms=duration_ms,
                )

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
                self._emit(NodeResult(
                    node_name=node.name, status=NodeStatus.RETRYING,
                    attempts=attempt + 1, error=exc,
                ))
                await asyncio.sleep(delay)

        # ---- 4. All retries exhausted ----
        logger.error(
            "[%s] FAILED after %d attempt(s): %s: %s",
            node.name,
            retry.max_retries + 1,
            type(last_error).__name__ if last_error else "?",
            last_error,
        )
        return NodeResult(
            node_name=node.name,
            status=NodeStatus.FAILED,
            error=last_error,
            attempts=retry.max_retries + 1,
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
