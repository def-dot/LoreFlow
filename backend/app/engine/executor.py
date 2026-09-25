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
from contextlib import nullcontext
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.registry import REGISTRY
from .condition import eval_condition
from .types import wired_ctx
from .pipeline import Node, RetryPolicy
from .types import (
    PipeLineExecutionError,
    NodeEventFunc,
    NodeResult,
    NodeStatus,
    SuspendExecution,
)

logger = logging.getLogger(__name__)


class PipeLineExecutor:
    """Executes a DAG concurrently, respecting node dependencies.

    Attributes:
        concurrency: Maximum number of nodes to run simultaneously.
                     ``None`` means unlimited.
    """

    def __init__(
        self,
        nodes: list[Node],
        ctx: dict[str, Any] | None = None,
        concurrency: int | None = None,
        on_event: NodeEventFunc | None = None,
        resume: dict[str, dict[str, Any]] | None = None,
    ):
        self.nodes: dict[str, Node] = {node.name: node for node in nodes}
        self.ctx: dict[str, Any] = ctx or {}
        self._semaphore: asyncio.Semaphore | None = asyncio.Semaphore(concurrency) if concurrency else None
        self.on_event = on_event
        self._resume: dict[str, dict[str, Any]] = resume or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self) -> dict[str, NodeResult]:
        events: dict[str, asyncio.Event] = {name: asyncio.Event() for name in self.nodes}
        results: dict[str, NodeResult] = {}
        tasks: list[asyncio.Task[None]] = []

        for node in self.nodes.values():
            saved = self._resume.get(node.name)
            if saved is not None and saved.get("status") in ("completed", "failed", "skipped", "upstream_skipped"):
                events[node.name].set()
                results[node.name] = NodeResult.model_validate(saved)
                if saved["status"] == "completed":
                    self.ctx[node.name] = saved.get("output")
            else:
                tasks.append(asyncio.create_task(self._run_node(node, events, results)))

        await asyncio.gather(*tasks, return_exceptions=True)

        failed = [name for name, r in results.items() if r.status == NodeStatus.FAILED]
        if failed:
            lines = [f"PipeLine 执行完成，{len(failed)} 个节点失败: {', '.join(failed)}"]
            for name in failed:
                if results[name].error:
                    lines.append(f"  {name}: {results[name].error}")
            raise PipeLineExecutionError("\n".join(lines), results)

        return results

    # ------------------------------------------------------------------
    # Single-node execution
    # ------------------------------------------------------------------

    async def _run_node(
        self,
        node: Node,
        events: dict[str, asyncio.Event],
        results: dict[str, NodeResult],
    ) -> None:
        result: NodeResult | None = None
        try:
            # ---- 1. Wait for dependencies ----
            for dep in node.depends_on:
                await events[dep].wait()

            # ---- 2. Cascading failure / skip ----
            dep_statuses = {dep: results[dep].status for dep in node.depends_on}
            blocked = [dep for dep, s in dep_statuses.items() if s in (NodeStatus.FAILED, NodeStatus.UPSTREAM_FAILED)]
            if blocked:
                logger.warning("[%s] Upstream failed, node not executed: %s", node.name, blocked)
                result = NodeResult(node_name=node.name, status=NodeStatus.UPSTREAM_FAILED)
                return

            if node.depends_on and all(s in (NodeStatus.SKIPPED, NodeStatus.UPSTREAM_SKIPPED) for s in dep_statuses.values()):
                logger.info("[%s] Upstream skipped, node not executed", node.name)
                result = NodeResult(node_name=node.name, status=NodeStatus.UPSTREAM_SKIPPED)
                return

            # ---- 3. Condition ----
            if node.condition is not None and not eval_condition(node.condition, self.ctx):
                logger.info("[%s] Skipped - condition not met", node.name)
                result = NodeResult(node_name=node.name, status=NodeStatus.SKIPPED)
                return

            # ---- 4. Execute with concurrency gate + retry ----
            if self.on_event is not None:
                await self.on_event(NodeResult(node_name=node.name, status=NodeStatus.RUNNING))
                
            result = await self._execute_with_retry(node)

        except asyncio.CancelledError:
            result = NodeResult(node_name=node.name, status=NodeStatus.CANCELLED)
        except Exception as exc:
            logger.exception("Unexpected error in executor for %s", node.name)
            result = NodeResult(node_name=node.name, status=NodeStatus.FAILED, error=str(exc))
        finally:
            results[result.node_name] = result
            if result.output:
                self.ctx[result.node_name] = result.output.model_dump()
            if self.on_event is not None:
                await self.on_event(result)
            events[node.name].set()

    async def _execute_with_retry(self, node: Node) -> NodeResult:
        """Run a node through its retry loop; returns the final ``NodeResult``."""
        retry = node.retry if isinstance(node.retry, RetryPolicy) else RetryPolicy(max_retries=node.retry or 0)

        retry_history: list[dict[str, Any]] = []
        last_error: str | None = None

        for attempt in range(retry.max_retries + 1):
            try:
                async with self._semaphore or nullcontext():
                    start = time.monotonic()
                    output = await self._call(node)
                    duration_ms = (time.monotonic() - start) * 1000

                logger.info("[%s] OK (attempt %d/%d, %.0f ms)", node.name, attempt + 1, retry.max_retries + 1, duration_ms)
                return NodeResult(
                    node_name=node.name, status=NodeStatus.COMPLETED,
                    output=output, attempts=attempt + 1, duration_ms=duration_ms,
                    retry_history=retry_history or None,
                )

            except SuspendExecution:
                raise

            except Exception as exc:
                last_error = str(exc)
                if not retry.should_retry(exc, attempt):
                    logger.error("[%s] FAIL non-retryable: %s", node.name, last_error)
                    break

                delay = retry.get_delay(attempt)
                logger.warning("[%s] RETRY %d/%d failed (%s), retrying in %.1fs", node.name, attempt + 1, retry.max_retries + 1, last_error, delay)
                retry_history.append({"attempt": attempt + 1, "error": last_error, "at": datetime.now().isoformat(timespec="seconds")})
                await asyncio.sleep(delay)

        logger.error("[%s] FAILED after %d attempt(s): %s", node.name, attempt + 1, last_error)
        return NodeResult(
            node_name=node.name, status=NodeStatus.FAILED,
            error=last_error, attempts=attempt + 1, retry_history=retry_history or None,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _call(self, node: Node) -> BaseModel:
        func_def = REGISTRY[node.type]
        resolved = wired_ctx(self.ctx, node.inputs or {})

        coro = func_def.func(func_def.input_schema(**resolved)) if func_def.input_schema else func_def.func()

        if node.timeout is not None:
            output = await asyncio.wait_for(coro, timeout=node.timeout)
        else:
            output = await coro
        return output