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
from typing import Any

from .condition import eval_condition
from .node import HumanRejected, Node, replay_review_edits, wired_view
from .types import (
    DAGExecutionError,
    NodeEventFunc,
    NodeResult,
    NodeStatus,
    RetryPolicy,
    SuspendExecution,
)

logger = logging.getLogger(__name__)


class DAGExecutor:
    """Executes a DAG concurrently, respecting node dependencies.

    Attributes:
        concurrency: Maximum number of nodes to run simultaneously.
                     ``None`` means unlimited.
    """

    def __init__(
        self,
        concurrency: int | None = None,
        on_event: NodeEventFunc | None = None,
    ):
        self._semaphore: asyncio.Semaphore | None = asyncio.Semaphore(concurrency) if concurrency else None
        self.on_event = on_event

    async def _emit(self, result: NodeResult) -> None:
        """Push a node state change to the ``on_event`` callback (if set)."""
        if self.on_event is not None:
            await self.on_event(result)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(
        self,
        nodes: dict[str, Node],
        inputs: dict[str, Any] | None = None,
        resume: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, NodeResult]:
        """Execute all *nodes* and return a mapping of node name → NodeResult.

        Args:
            nodes: All nodes in the DAG (keyed by name).
            inputs: Initial key-value pairs placed into the shared context.
            resume: 重启恢复用的节点快照 ``{name: {status, output, ...}}``；

        Returns:
            Dict mapping each node name to its :class:`NodeResult`.

        Raises:
            DAGExecutionError: If one or more nodes ultimately failed.
        """
        # ----- shared state -----
        ctx: dict[str, Any] = dict(inputs) if inputs else {}
        events: dict[str, asyncio.Event] = {name: asyncio.Event() for name in nodes}
        tasks: dict[str, asyncio.Future[NodeResult]] = {}

        # ----- resume: 已完成/跳过（条件或级联）的节点作为既成事实直接恢复 -----
        resume = resume or {}
        loop = asyncio.get_running_loop()

        for name, saved in resume.items():
            if saved.get("status") not in ("completed", "skipped", "upstream_skipped"):
                continue

            events[name].set()
            restored = NodeResult(
                node_name=name,
                status=NodeStatus(saved.get("status")),
                output=saved.get("output"),
                attempts=saved.get("attempts"),
                duration_ms=saved.get("duration_ms") or 0.0,
            )
            tasks[name] = loop.create_future()
            tasks[name].set_result(restored)

            ctx[name] = saved.get("output")
            
            node_type = nodes[name].node_type
            if node_type is not None and node_type.name == "human":
                replay_review_edits(ctx, saved.get("output"))

        # ----- spawn remaining nodes -----
        for node in nodes.values():
            if node.name in tasks:
                continue
            tasks[node.name] = asyncio.create_task(self._run_node(node, ctx, tasks, events))

        # ----- wait for completion -----
        await asyncio.gather(*tasks.values(), return_exceptions=True)

        results = {name: task.result() for name, task in tasks.items()}
        # ----- surface failures as DAGExecutionError -----
        failed = [name for name, r in results.items() if r.status == NodeStatus.FAILED]
        if failed:
            # 构建详细失败消息：节点名 + 异常类型 + 消息
            fail_lines = [f"DAG 执行完成，{len(failed)} 个节点失败: {', '.join(failed)}"]
            for name in failed:
                result = results[name]
                if result.error is not None:
                    fail_lines.append(f"  {name}: {type(result.error).__name__}: {result.error}")
            raise DAGExecutionError("\n".join(fail_lines), results)

        return results

    # ------------------------------------------------------------------
    # Single-node execution
    # ------------------------------------------------------------------

    async def _run_node(
        self,
        node: Node,
        ctx: dict[str, Any],
        tasks: dict[str, asyncio.Future[NodeResult]],
        events: dict[str, asyncio.Event],
    ) -> NodeResult:
        """Lifecycle of a single node: 等依赖 → 失败/跳过级联 → 条件判断 → 带重试执行 → 收尾。

        Task-level safety net: 除挂起信号（SuspendExecution 向上穿透、
        本节点挂起已带 REVIEWING result 供 emit）外，无论执行如何退出，
        必返回终态 NodeResult。同步(events)/事件汇报两件事都在 finally
        收口——全部从这同一个 result 推导。
        """
        result: NodeResult | None = None
        try:
            # ---- 1. Wait for dependencies ----
            for dep in node.depends_on:
                await events[dep].wait()

            # ---- 2. Cascading failure / cascading skip ----
            dep_status = {dep: tasks[dep].result().status for dep in node.depends_on}
            # 失败优先级最高，压过一切跳过规则
            blocked = [
                dep
                for dep, s in dep_status.items()
                if s in (NodeStatus.FAILED, NodeStatus.UPSTREAM_FAILED)
            ]
            if blocked:
                logger.warning("[%s] Upstream failed, node not executed: %s", node.name, blocked)
                result = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.UPSTREAM_FAILED,
                )
                return result
            
            # 汇合语义（any-success）：全部依赖被跳过才跟着跳过（级联）；
            if node.depends_on and all(
                s in (NodeStatus.SKIPPED, NodeStatus.UPSTREAM_SKIPPED) for s in dep_status.values()
            ):
                logger.info("[%s] Upstream skipped, node not executed", node.name)
                result = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.UPSTREAM_SKIPPED,
                )
                return result

            # ---- 3. Evaluate condition (branching) ----
            if node.condition is not None:
                try:
                    should_run = eval_condition(node.condition, wired_view(ctx, node.inputs))
                except Exception as exc:
                    logger.error(
                        "[%s] Condition raised %s: %s - skipping node",
                        node.name,
                        type(exc).__name__,
                        exc,
                    )
                    should_run = False

                if not should_run:
                    logger.info("[%s] Skipped - condition not met", node.name)
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.SKIPPED,
                    )
                    return result

            # ---- 4. Execute with retry ----
            retry = node.retry or RetryPolicy(max_retries=0)
            last_error: Exception | None = None
            await self._emit(NodeResult(node_name=node.name, status=NodeStatus.RUNNING))

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
                    # 人工审核通过后的修订回放共享上下文（与 resume 恢复同一条路径）
                    if node.node_type is not None and node.node_type.name == "human":
                        replay_review_edits(ctx, output)
                    logger.info(
                        "[%s] OK  completed  (attempt %d/%d, %.0f ms)",
                        node.name,
                        attempt + 1,
                        retry.max_retries + 1,
                        duration_ms,
                    )
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.COMPLETED,
                        output=output,
                        attempts=attempt + 1,
                        duration_ms=duration_ms,
                    )
                    return result

                except HumanRejected as exc:
                    # 人工拒绝是终局决策：不进重试循环，FAILED 结果携带拒绝详情
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.FAILED,
                        output=exc.output,
                        error=exc,
                        attempts=attempt + 1,
                    )
                    return result

                except SuspendExecution as exc:
                    # 本节点审批挂起：REVIEWING + 审核视图（output）随 finally 的
                    # emit 落快照；重新抛出让 run 走挂起收尾。
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.REVIEWING,
                        output=exc.results,
                    )
                    raise

                except Exception as exc:
                    last_error = exc

                    if not retry.should_retry(exc, attempt):
                        logger.error(
                            "[%s] FAIL  non-retryable / retries exhausted: %s: %s",
                            node.name,
                            type(exc).__name__,
                            exc,
                        )
                        break

                    delay = retry.get_delay(attempt)
                    logger.warning(
                        "[%s] RETRY  attempt %d/%d failed (%s: %s), retrying in %.1f s ...",
                        node.name,
                        attempt + 1,
                        retry.max_retries + 1,
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    await self._emit(
                        NodeResult(
                            node_name=node.name,
                            status=NodeStatus.RETRYING,
                            attempts=attempt + 1,
                            error=exc,
                        )
                    )
                    await asyncio.sleep(delay)

            # ---- 5. All retries exhausted ----
            logger.error(
                "[%s] FAILED after %d attempt(s): %s: %s",
                node.name,
                attempt + 1,
                type(last_error).__name__ if last_error else "?",
                last_error,
            )
            result = NodeResult(
                node_name=node.name,
                status=NodeStatus.FAILED,
                error=last_error,
                attempts=attempt + 1,
            )
        except asyncio.CancelledError:
            result = NodeResult(
                node_name=node.name,
                status=NodeStatus.CANCELLED,
            )
        except Exception as exc:
            # Should not happen — the code above is defensive, but guard anyway
            logger.exception("Unexpected error in executor for %s", node.name)
            result = NodeResult(
                node_name=node.name,
                status=NodeStatus.FAILED,
                error=exc,
            )
        finally:
            if result:
                await self._emit(result)
            events[node.name].set()
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _call(node: Node, ctx: dict[str, Any]) -> Any:
        """Invoke *node.func* with timeout if configured.

        统一在此组装接线视图（``Node.inputs`` + 保留键 ``_node``/``_upstream``）；
        循环节点例外——body 子流水线要直接读写共享上下文（输出累积进顶层）。
        """
        if node.metadata.get("loop"):
            target: dict[str, Any] = ctx
        else:
            target = wired_view(ctx, node.inputs)
            target["_node"] = node.name
            if node.depends_on:
                target["_upstream"] = {d: ctx.get(d) for d in node.depends_on}
        coro = node.func(target)
        if node.timeout is not None:
            return await asyncio.wait_for(coro, timeout=node.timeout)
        return await coro
