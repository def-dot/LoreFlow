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
from typing import Any

from app.registry import REGISTRY
from .condition import eval_condition
from .types import HumanRejected, NodeContext, current_node_ctx, wired_ctx
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

    字段 = 构造输入与配置（nodes/ctx/semaphore/on_event，回答"执行什么"）；
    events / results 是 execute 的过程状态（回答"这次怎么走"），作为参数传给 _run_node。
    两个平面：ctx 是数据流（input + 成功节点 output，给 $ / condition / 入参），
    results 是执行状态（NodeResult，给依赖级联 / 返回值 / UI）。
    同一实例并发调用 execute 不安全，顺序复用安全（execute 不改 nodes，ctx 语义上属于单次运行）。

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
        # 执行对象（nodes）与共享上下文（ctx）都从构造器进：
        # nodes 是要执行的图，ctx 是执行器推进的工作流数据；
        # 控制流簿记（events/tasks）是 execute 的过程状态，留在方法内
        self.nodes: dict[str, Node] = {node.name: node for node in nodes}
        self.ctx: dict[str, Any] = ctx if ctx is not None else {}
        self._semaphore: asyncio.Semaphore | None = asyncio.Semaphore(concurrency) if concurrency else None
        self.on_event = on_event
        self._resume: dict[str, dict[str, Any]] = resume or {}

    async def _emit(self, result: NodeResult) -> None:
        """Push a node state change to the ``on_event`` callback (if set)."""
        if self.on_event is not None:
            await self.on_event(result)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self) -> dict[str, NodeResult]:
        """Execute ``self.nodes`` and return a mapping of node name → NodeResult.

        Returns:
            Dict mapping each node name to its :class:`NodeResult`.

        Raises:
            DAGExecutionError: If one or more nodes ultimately failed.
        """
        # ----- control-flow bookkeeping -----
        events: dict[str, asyncio.Event] = {name: asyncio.Event() for name in self.nodes}
        results: dict[str, NodeResult] = {}
        tasks: list[asyncio.Task[None]] = []

        for node in self.nodes.values():
            saved = self._resume.get(node.name)
            if saved is not None and saved.get("status") in ("completed", "skipped", "upstream_skipped"):
                events[node.name].set()
                resumed = NodeResult(
                    node_name=node.name,
                    status=NodeStatus(saved.get("status")),
                    output=saved.get("output"),
                    attempts=saved.get("attempts"),
                    duration_ms=saved.get("duration_ms") or 0.0,
                )
                results[node.name] = resumed
                if resumed.status == NodeStatus.COMPLETED:
                    self.ctx[node.name] = resumed.output
            else:
                tasks.append(asyncio.create_task(self._run_node(node, events, results)))

        # ----- wait for completion -----
        await asyncio.gather(*tasks, return_exceptions=True)

        # ----- surface failures as DAGExecutionError -----
        failed = [name for name, r in results.items() if r.status == NodeStatus.FAILED]
        if failed:
            fail_lines = [f"PipeLine 执行完成，{len(failed)} 个节点失败: {', '.join(failed)}"]
            for name in failed:
                result = results[name]
                if result.error is not None:
                    fail_lines.append(f"  {name}: {type(result.error).__name__}: {result.error}")
            raise PipeLineExecutionError("\n".join(fail_lines), results)

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
        """Lifecycle of a single node: 等依赖 → 失败/跳过级联 → 条件判断 → 带重试执行 → 收尾。

        收尾在 finally 里直接登记：NodeResult 进 results，成功 output 落 self.ctx。
        """
        result: NodeResult | None = None
        try:
            # ---- 1. Wait for dependencies ----
            for dep in node.depends_on:
                await events[dep].wait()

            # ---- 2. Cascading failure / cascading skip ----
            dep_status = {dep: results[dep].status for dep in node.depends_on}
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
                return

            # 汇合语义（any-success）：全部依赖被跳过才跟着跳过（级联）；
            if node.depends_on and all(
                s in (NodeStatus.SKIPPED, NodeStatus.UPSTREAM_SKIPPED) for s in dep_status.values()
            ):
                logger.info("[%s] Upstream skipped, node not executed", node.name)
                result = NodeResult(
                    node_name=node.name,
                    status=NodeStatus.UPSTREAM_SKIPPED,
                )
                return

            # ---- 3. Evaluate condition (branching) ----
            if node.condition is not None:
                should_run = eval_condition(node.condition, self.ctx)

                if not should_run:
                    logger.info("[%s] Skipped - condition not met", node.name)
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.SKIPPED,
                    )
                    return

            # ---- 4. Execute with retry ----
            await self._emit(NodeResult(node_name=node.name, status=NodeStatus.RUNNING))
            retry = node.retry if isinstance(node.retry, RetryPolicy) else RetryPolicy(max_retries=node.retry or 0)
            last_error: Exception | None = None
            retry_history: list[dict[str, Any]] = []

            for attempt in range(retry.max_retries + 1):
                try:
                    start = time.monotonic()

                    if self._semaphore:
                        async with self._semaphore:
                            output = await self._call(node)
                    else:
                        output = await self._call(node)

                    duration_ms = (time.monotonic() - start) * 1000

                    # success（output 落 ctx 由收尾的 _record 统一登记）
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
                        retry_history=retry_history or None,
                    )
                    return

                except HumanRejected as exc:
                    # 人工拒绝是终局决策：不进重试循环，FAILED 结果携带拒绝详情
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.FAILED,
                        output=exc.output,
                        error=exc,
                        attempts=attempt + 1,
                        retry_history=retry_history or None,
                    )
                    return

                except SuspendExecution as exc:
                    # 本节点审批挂起：REVIEWING + 审核视图（payload）随 finally 的
                    # emit 落快照；重新抛出让 run 走挂起收尾。
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.REVIEWING,
                        output=exc.results,
                        retry_history=retry_history or None,
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
                    retry_history.append({
                        "attempt": attempt + 1,
                        "error": str(exc),
                        "at": datetime.now().isoformat(timespec="seconds"),
                    })
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
                retry_history=retry_history or None,
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
            if result is not None:
                results[result.node_name] = result
                if result.status == NodeStatus.COMPLETED:
                    self.ctx[result.node_name] = result.output
                await self._emit(result)
            events[node.name].set()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _call(self, node: Node) -> Any:
        """Invoke the node function (from REGISTRY) with timeout and output validation."""
        func_def = REGISTRY[node.type]
        resolved = wired_ctx(self.ctx, node.inputs or {})

        # 注入 NodeContext（所有节点都设置，需要的函数通过 current_node_ctx.get() 读取）
        saved = self._resume.get(node.name)
        stored_decision = None
        if saved and saved.get("output"):
            stored_decision = saved["output"].get("decision")
        current_node_ctx.set(NodeContext(node_name=node.name, stored_decision=stored_decision))

        coro = func_def.func(func_def.input_schema(**resolved)) if func_def.input_schema else func_def.func()

        output = await asyncio.wait_for(coro, timeout=node.timeout) if node.timeout is not None else await coro
        return self._validate_output(node, output)

    # ---- output validation ----

    @staticmethod
    def _validate_output(node: Node, output: Any) -> dict[str, Any]:
        """若 node 声明了 output_schema，用 Pydantic 校验并转 dict。"""
        if output is None:
            return output
        schema = node.resolve_output_schema()
        if schema is not None:
            if isinstance(output, schema):
                return output.model_dump()
            return schema.model_validate(output).model_dump()
        return output
