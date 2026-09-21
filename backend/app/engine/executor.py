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
import inspect
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.registry import REGISTRY
from .condition import eval_condition
from .types import HumanRejected, wired_ctx
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
    events/tasks 是 execute 的过程状态（回答"这次怎么走"），留在方法帧里。
    ctx 为共享可变状态——同一实例并发调用 execute 不安全，顺序复用安全
    （execute 不改 nodes，ctx 语义上属于单次运行）。

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
    ):
        # 执行对象（nodes）与共享上下文（ctx）都从构造器进：
        # nodes 是要执行的图，ctx 是执行器推进的工作流数据；
        # 控制流簿记（events/tasks）是 execute 的过程状态，留在方法内
        self.nodes: dict[str, Node] = {node.name: node for node in nodes}
        self.ctx: dict[str, Any] = ctx if ctx is not None else {}
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
        resume: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, NodeResult]:
        """Execute ``self.nodes`` and return a mapping of node name → NodeResult.

        Args:
            resume: 重启恢复用的节点快照 ``{name: {status, output, ...}}``；

        Returns:
            Dict mapping each node name to its :class:`NodeResult`.

        Raises:
            DAGExecutionError: If one or more nodes ultimately failed.
        """
        # ----- control-flow bookkeeping（图与数据在 self.nodes/self.ctx）-----
        events: dict[str, asyncio.Event] = {name: asyncio.Event() for name in self.nodes}
        tasks: dict[str, asyncio.Future[NodeResult]] = {}
        resume = resume or {}
        loop = asyncio.get_running_loop()

        for node in self.nodes.values():
            saved = resume.get(node.name)
            if saved is not None and saved.get("status") in ("completed", "skipped", "upstream_skipped"):
                events[node.name].set()
                restored = NodeResult(
                    node_name=node.name,
                    status=NodeStatus(saved.get("status")),
                    output=saved.get("output"),
                    attempts=saved.get("attempts"),
                    duration_ms=saved.get("duration_ms") or 0.0,
                )
                tasks[node.name] = loop.create_future()
                tasks[node.name].set_result(restored)
                self.ctx[node.name] = saved.get("output")
            else:
                tasks[node.name] = asyncio.create_task(self._run_node(node, tasks, events))

        # ----- wait for completion -----
        await asyncio.gather(*tasks.values(), return_exceptions=True)

        results = {name: task.result() for name, task in tasks.items()}
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
        tasks: dict[str, asyncio.Future[NodeResult]],
        events: dict[str, asyncio.Event],
    ) -> NodeResult:
        """Lifecycle of a single node: 等依赖 → 失败/跳过级联 → 条件判断 → 带重试执行 → 收尾。
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
                    should_run = eval_condition(node.condition, wired_ctx(self.ctx, node.inputs))
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

                    # success
                    self.ctx[node.name] = output
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
                    return result

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
                    return result

                except SuspendExecution as exc:
                    # 本节点审批挂起：REVIEWING + 审核视图（output）随 finally 的
                    # emit 落快照；重新抛出让 run 走挂起收尾。
                    result = NodeResult(
                        node_name=node.name,
                        status=NodeStatus.REVIEWING,
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
            await self._emit(result)
            events[node.name].set()
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _call(self, node: Node) -> Any:
        """Invoke the node function (from REGISTRY) with timeout, output validation and deep $-resolution."""
        target = wired_ctx(self.ctx, node.inputs)
        target["_node"] = node.name
        self._resolve_deep_inputs(target)

        func = REGISTRY[node.type].func
        kwargs = self._build_kwargs(func, target)
        coro = func(**kwargs)

        output = await asyncio.wait_for(coro, timeout=node.timeout) if node.timeout is not None else await coro
        return self._validate_output(node, output)

    # ---- argument resolution ----

    @staticmethod
    def _build_kwargs(func: Callable, target: dict[str, Any]) -> dict[str, Any]:
        """从 target 构造 func 的 kwargs。三种派发路径：
        1. ctx 参数 → 传整个 target
        2. BaseModel 参数 → 从 target 字段自动构造实例
        3. 普通 kwargs → 按名称匹配 target 键
        """
        sig = inspect.signature(func)

        # 1) ctx 风格
        if "ctx" in sig.parameters:
            return {"ctx": target}

        # 识别 BaseModel 参数
        model_params: dict[str, type[BaseModel]] = {}
        for pname, param in sig.parameters.items():
            if pname.startswith("_") or param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            ann = param.annotation
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                model_params[pname] = ann

        # 2) BaseModel 派发
        if model_params:
            kwargs: dict[str, Any] = {}
            remaining = dict(target)

            for pname, model_cls in model_params.items():
                fields = set(model_cls.model_fields)
                kwargs[pname] = model_cls(**{k: remaining.pop(k) for k in fields if k in remaining})

            # 处理剩余参数：_前缀透传、VAR_KEYWORD 收集、普通参数匹配
            for pname, param in sig.parameters.items():
                if pname in kwargs:
                    continue
                if param.kind == inspect.Parameter.VAR_KEYWORD:
                    kwargs[pname] = {k: v for k, v in remaining.items() if not k.startswith("_")}
                    if "_approver" in remaining:
                        kwargs[pname]["_approver"] = remaining["_approver"]
                    remaining.clear()
                elif pname in target:
                    kwargs[pname] = target[pname]

            return kwargs

        # 3) 普通 kwargs 派发
        kwargs = {p: target[p] for p in sig.parameters if p in target}
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            kwargs.update({k: v for k, v in target.items() if k not in kwargs and not k.startswith("_")})
            # VAR_KEYWORD 节点需要 _approver 来驱动 human 节点
            if "_approver" in target and "_approver" not in kwargs:
                kwargs["_approver"] = target["_approver"]
        return kwargs

    # ---- deep $-resolution ----

    def _resolve_deep_inputs(self, target: dict[str, Any]) -> None:
        """对 dict 类型的 input 值递归解析 $ 引用（如 review 卡片模板）。"""
        def _resolve(obj: Any) -> Any:
            if isinstance(obj, str) and obj.startswith("$"):
                val: Any = self.ctx
                for part in obj[1:].split("."):
                    if not isinstance(val, Mapping) or part not in val:
                        return obj
                    val = val[part]
                return val
            if isinstance(obj, dict):
                return {k: _resolve(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_resolve(v) for v in obj]
            return obj

        for k, v in list(target.items()):
            if isinstance(v, dict):
                target[f"_raw_{k}"] = v
                target[k] = _resolve(v)

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
