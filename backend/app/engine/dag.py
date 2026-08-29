"""
The DAG (Directed Acyclic Graph) class — the user-facing API.

Usage::

    from app.engine import DAG, RetryPolicy

    dag = DAG("my_pipeline")

    @dag.node("fetch", retry=RetryPolicy(max_retries=3))
    async def fetch(ctx):
        return await api.get("/data")

    @dag.node("process", depends_on=["fetch"])
    async def process(ctx):
        data = ctx["fetch"]
        return transform(data)

    results = await dag.run()
"""

from __future__ import annotations

import asyncio
import logging
import pprint
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from .executor import DAGExecutor
from .node import ApproverFunc, ConditionFunc, Node, NodeFunc
from .types import DAGExecutionError, NodeResult, NodeStatus, RetryPolicy
from .validate import validate_graph, validate_inputs, validate_params

logger = logging.getLogger(__name__)


async def terminal_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Interactive approver: ask the reviewer on the terminal (y/n).

    Pass explicitly to ``load_dag(approver=...)`` or ``DAG(approver=...)``
    for runs driven from a terminal; on EOF (e.g. CI) the review is rejected.
    """
    print(f"  Payload:\n{pprint.pformat(payload, sort_dicts=False)}")
    while True:
        try:
            answer = (await asyncio.to_thread(input, "  Approve? [y/N]: ")).strip().lower()
        except EOFError:
            answer = "n"  # no terminal input available -> reject
        if answer in ("", "n", "no"):
            return {"approve": False}
        if answer in ("y", "yes"):
            return {"approve": True}
        print("  Please answer y or n")


class DAG:
    """A Directed Acyclic Graph workflow.

    Register nodes with the :meth:`node` decorator or :meth:`add_node`,
    then execute the entire graph with :meth:`run`.

    Parameters:
        name: A human-readable label for this workflow (used in logs & diagrams).
        params: 运行时输入参数声明 ``{name: {default, required, ...}}``
                （load_dag 传入 YAML 顶层 ``inputs`` 原文）。默认值/必填键不
                单独拆开传，由 :attr:`default_inputs` / :attr:`required_inputs`
                按需派生。
        on_event: Optional async callback invoked on every node state change
                  (running/retrying/completed/failed/upstream_failed/skipped/
                  upstream_skipped/cancelled) and awaited. Useful for live
                  progress monitoring (e.g. a web UI).
    """

    def __init__(
        self,
        name: str = "dag",
        params: dict[str, dict[str, Any]] | None = None,
        on_event: Callable[[NodeResult], Awaitable[None]] | None = None,
        approver: ApproverFunc | None = None,
    ):
        self.name = name
        self.params = params if params else {}
        self.on_event = on_event
        self.approver = approver
        self._nodes: dict[str, Node] = {}

    @property
    def default_inputs(self) -> dict[str, Any]:
        """声明了 ``default`` 的参数 → 默认值（必填键也不例外）。

        :meth:`run` 未传 inputs 时以本视图顶班，输入校验也看回退后的
        生效输入（defaults 算已提供）；API 边界在 create_run 校验原始
        inputs，required 即使有 default 也须显式传入。
        """
        return {
            name: spec["default"]
            for name, spec in self.params.items()
            if "default" in spec
        }

    @property
    def required_inputs(self) -> list[str]:
        """``required: true`` 的参数键（run 前缺失即 ``ValueError``）。"""
        return [name for name, spec in self.params.items() if spec.get("required")]

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_node(self, node: Node) -> Node:
        """Register a pre-built :class:`Node` instance.

        Raises:
            ValueError: If a node with the same name already exists, or
                func/condition is not callable.
        """
        if node.name in self._nodes:
            raise ValueError(f"节点名重复: {node.name!r}")
        if not callable(node.func):
            raise ValueError(
                f"节点 {node.name!r}: func 必须是可调用对象，实际是 {type(node.func).__name__}"
            )
        if node.condition is not None and not isinstance(node.condition, (str, bool)):
            raise ValueError(
                f"节点 {node.name!r}: condition 必须是表达式字符串或布尔常量，"
                f"实际是 {type(node.condition).__name__}"
            )
        self._nodes[node.name] = node
        return node

    def node(
        self,
        name: str,
        depends_on: list[str] | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
        condition: str | bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Callable[[NodeFunc], NodeFunc]:
        """Decorator: register an async function as a DAG node.

        Example::

            @dag.node("step_2", depends_on=["step_1"], retry=RetryPolicy(3))
            async def step_2(ctx):
                return process(ctx["step_1"])
        """

        def decorator(func: NodeFunc) -> NodeFunc:
            self.add_node(
                Node(
                    name=name,
                    func=func,
                    label=name,
                    depends_on=depends_on or [],
                    retry=retry,
                    timeout=timeout,
                    condition=condition,
                    metadata=metadata or {},
                )
            )
            return func

        return decorator

    # ------------------------------------------------------------------
    # Loop support
    # ------------------------------------------------------------------

    def loop_node(
        self,
        name: str,
        body_nodes: list[Node],
        condition: ConditionFunc,
        depends_on: list[str] | None = None,
        max_iterations: int = 100,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
    ) -> Node:
        """Add a loop node that iterates a sub-DAG until a condition is met.

        The *body_nodes* form a mini-DAG that is re-executed on each
        iteration.  Results accumulate in the shared context across
        iterations.

        Args:
            name: Unique node name for the loop.
            body_nodes: Nodes that make up the loop body (a sub-DAG).
            condition: 与节点条件同一形态 ``(视图) -> bool``；视图 = 累积
                       上下文 + 每轮注入的 ``iteration``（从 1 起）。
                       Return ``True`` to **continue** looping（YAML 声明
                       层写 ``condition: iteration < 3`` 即可）。
            depends_on: Upstream nodes the loop waits on before its first iteration.
            max_iterations: Safety cap on iterations.
            retry: Retry policy applied to each iteration of the whole sub-DAG.
            timeout: Per-iteration timeout in seconds.

        Returns:
            The registered loop :class:`Node`.
        """
        if not body_nodes:
            raise ValueError(f"循环节点 {name!r} 至少需要一个 body 节点")
        if not callable(condition):
            raise ValueError(f"循环节点 {name!r}: condition 必须是可调用对象")

        # Build the sub-DAG
        sub = DAG(f"{name}_body")
        for n in body_nodes:
            sub.add_node(n)

        # body 图结构注册期同查（缺失依赖/环）—— 与声明层递归
        # validate_nodes 的时机对齐，不等首轮 sub.run 才报
        body_errors = sub.validate()
        if body_errors:
            raise ValueError(
                f"循环节点 {name!r} 的 body 校验失败:\n  " + "\n  ".join(body_errors)
            )

        async def loop_func(ctx: dict[str, Any]) -> dict[str, Any]:
            iteration = 0
            while iteration < max_iterations:
                iteration += 1
                logger.info("[%s] loop iteration %d / %d", name, iteration, max_iterations)

                # Run the body sub-DAG。直接驱动执行器并共享外层上下文：
                # body 已在注册期校验过，且外层上下文对无 params 的 body
                # 不是「输入」，不走 run() 的输入白名单。body 失败时已完成
                # 节点的输出已在共享上下文，是否终止循环由 condition 决定
                try:
                    await DAGExecutor(nodes=sub.nodes, ctx=ctx, on_event=sub.on_event).execute()
                except DAGExecutionError:
                    pass

                # Evaluate loop condition on a view with ``iteration`` injected
                try:
                    should_continue = condition({**ctx, "iteration": iteration})
                except Exception as exc:
                    logger.error(
                        "[%s] Loop condition raised %s — exiting loop",
                        name,
                        type(exc).__name__,
                    )
                    break

                if not should_continue:
                    logger.info("[%s] loop finished after %d iteration(s)", name, iteration)
                    break
            else:
                logger.warning("[%s] max iterations (%d) reached", name, max_iterations)

            # 输出 = 累积上下文的快照（浅拷贝）：直接返回 ctx 会让执行器的
            # ctx[name] = output 形成自引用，JSON 序列化（快照落库）报
            # Circular reference detected，run 卡死在 running
            return dict(ctx)

        node = Node(
            name=name,
            func=loop_func,
            depends_on=depends_on or [],
            retry=retry,
            timeout=timeout,
            # loop 标记供 executor 直用共享上下文；
            # type/type_label 供 to_mermaid 小字行（无注册表引用的类型）
            metadata={
                "loop": True,
                "max_iterations": max_iterations,
                "type": "loop",
                "type_label": "循环",
            },
        )
        self.add_node(node)
        return node

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """结构校验 —— 工作流本身的合法性
        """
        errors: list[str] = []

        if not self._nodes:
            errors.append("DAG 没有节点")
            return errors

        # 程序化 DAG 无 config —— 按函数入参形状合成（只用到 inputs/nodes 键）
        errors.extend(validate_params({"inputs": self.params, "nodes": self._nodes}))

        edges = {name: node.depends_on for name, node in self._nodes.items()}
        errors.extend(validate_graph(edges))
        return errors

    # ------------------------------------------------------------------
    # Topological order (informational)
    # ------------------------------------------------------------------

    def topological_order(self) -> list[str]:
        """Return node names in topological order (Kahn's algorithm).

        Useful for visualising the execution plan.
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        dependents: dict[str, list[str]] = {n: [] for n in self._nodes}

        for node in self._nodes.values():
            for dep in node.depends_on:
                dependents[dep].append(node.name)
                in_degree[node.name] += 1

        queue = [n for n, d in in_degree.items() if d == 0]
        order: list[str] = []

        while queue:
            n = queue.pop(0)
            order.append(n)
            for downstream in dependents[n]:
                in_degree[downstream] -= 1
                if in_degree[downstream] == 0:
                    queue.append(downstream)

        return order

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def run(
        self,
        inputs: dict[str, Any] | None = None,
        concurrency: int | None = None,
        resume: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, NodeResult]:
        """Execute the DAG.

        Args:
            inputs: Initial data placed into the shared context before
                    execution (defaults to ``default_inputs``).
            concurrency: Maximum number of nodes running at once
                         (``None`` = unlimited).
            resume: 重启恢复用的节点快照（见 DAGExecutor.execute）。

        Returns:
            A dict mapping every node name to its :class:`NodeResult`.

        Raises:
            ValueError: 结构校验失败，或（声明了 params 时）输入不合法。
            DAGExecutionError: If any nodes failed.
        """
        errors = self.validate()
        if errors:
            raise ValueError("DAG 结构无效:\n  " + "\n  ".join(errors))

        if inputs is None:
            inputs = self.default_inputs

        if self.params:
            errors = validate_inputs(inputs, self.params)
            if errors:
                raise ValueError("\n".join(errors))

        logger.info("== DAG %r starting (%d nodes) ==", self.name, len(self._nodes))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Topological order: %s", " -> ".join(self.topological_order()))

        # _approver 注入共享上下文：human 节点的审核结果来自当前运行
        ctx: dict[str, Any] = dict(inputs or {})
        if self.approver is not None:
            ctx["_approver"] = self.approver

        executor = DAGExecutor(nodes=self._nodes, ctx=ctx, concurrency=concurrency, on_event=self.on_event)
        return await executor.execute(resume=resume)

    # ------------------------------------------------------------------
    # Visualisation
    # ------------------------------------------------------------------

    def to_mermaid(self) -> str:
        """Render the DAG as a Mermaid flowchart (for docs / debugging).

        节点文案：大字行 = 节点 label（未声明回退节点名）；小字行 = 类型名 ·
        类型 label（经 node_type 引用读取；无引用的类型回退 metadata），
        再加条件 [?] 与重试 [Rn] 角标。
        """
        lines = ["graph TD"]
        for node in self._nodes.values():
            nid = node.name.replace(" ", "_").replace("-", "_")
            main_text = node.label or node.name

            small = []
            # 类型身份经 node_type 引用（定义—实例）；无引用的类型（程序化
            # loop_node 等）回退 metadata
            type_name = node.node_type.name if node.node_type else node.metadata.get("type")
            type_label = node.node_type.label if node.node_type else node.metadata.get("type_label")
            if type_name:
                small.append(f"{type_name} · {type_label}" if type_label else type_name)
            if node.condition:
                small.append("[?]")
            if node.retry and node.retry.max_retries:
                small.append(f"[R{node.retry.max_retries}]")
            text = main_text + (f"<br/><i>{' '.join(small)}</i>" if small else "")
            lines.append(f'    {nid}["{text}"]')
            for dep in node.depends_on:
                did = dep.replace(" ", "_").replace("-", "_")
                lines.append(f"    {did} --> {nid}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def nodes(self) -> dict[str, Node]:
        """Return all registered nodes (read-only view)."""
        return dict(self._nodes)

    @property
    def node_names(self) -> list[str]:
        """Return the list of registered node names."""
        return list(self._nodes)

    @property
    def human_nodes(self) -> list[Node]:
        """Nodes that pause for human review（类型为 human）."""
        return [
            node
            for node in self._nodes.values()
            if node.node_type is not None and node.node_type.name == "human"
        ]

    def __repr__(self) -> str:
        return f"DAG({self.name!r}, nodes={list(self._nodes)})"

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, name: str) -> bool:
        return name in self._nodes
