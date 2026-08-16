"""
The DAG (Directed Acyclic Graph) class — the user-facing API.

Usage::

    from dag_flow import DAG, RetryPolicy

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
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from executor import DAGExecutionError, DAGExecutor
from node import ApproverFunc, ConditionFunc, HumanRejected, Node, NodeFunc
from schems import NodeResult, RetryPolicy

logger = logging.getLogger(__name__)


async def terminal_approver(node_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Interactive approver: ask the reviewer on the terminal (y/n).

    Pass explicitly to :meth:`DAG.human_node` or ``load_dag(approver=...)``
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
        default_inputs: Initial context applied when :meth:`run` is called
                        without explicit ``inputs``.
    """

    def __init__(
        self,
        name: str = "dag",
        default_inputs: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.default_inputs = default_inputs if default_inputs else {}
        self._nodes: Dict[str, Node] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def add_node(self, node: Node) -> Node:
        """Register a pre-built :class:`Node` instance.

        Raises:
            ValueError: If a node with the same name already exists.
        """
        if node.name in self._nodes:
            raise ValueError(f"Duplicate node name: {node.name!r}")
        self._nodes[node.name] = node
        return node

    def node(
        self,
        name: str,
        depends_on: Optional[List[str]] = None,
        retry: Optional[RetryPolicy] = None,
        timeout: Optional[float] = None,
        condition: Optional[ConditionFunc] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Decorator: register an async function as a DAG node.

        Example::

            @dag.node("step_2", depends_on=["step_1"], retry=RetryPolicy(3))
            async def step_2(ctx):
                return process(ctx["step_1"])
        """
        def decorator(func: NodeFunc) -> NodeFunc:
            self.add_node(Node(
                name=name,
                func=func,
                depends_on=depends_on or [],
                retry=retry,
                timeout=timeout,
                condition=condition,
                metadata=metadata or {},
            ))
            return func
        return decorator

    # ------------------------------------------------------------------
    # Loop support
    # ------------------------------------------------------------------

    def loop_node(
        self,
        name: str,
        body_nodes: List[Node],
        condition: Callable[[Dict[str, Any], int], bool],
        depends_on: Optional[List[str]] = None,
        max_iterations: int = 100,
        retry: Optional[RetryPolicy] = None,
        timeout: Optional[float] = None,
    ) -> Node:
        """Add a loop node that iterates a sub-DAG until a condition is met.

        The *body_nodes* form a mini-DAG that is re-executed on each
        iteration.  Results accumulate in the shared context across
        iterations.

        Args:
            name: Unique node name for the loop.
            body_nodes: Nodes that make up the loop body (a sub-DAG).
            condition: ``(ctx, iteration) -> bool``.
                       Return ``True`` to **continue** looping.
            depends_on: Upstream nodes the loop waits on before its first iteration.
            max_iterations: Safety cap on iterations.
            retry: Retry policy applied to each iteration of the whole sub-DAG.
            timeout: Per-iteration timeout in seconds.

        Returns:
            The registered loop :class:`Node`.
        """
        if not body_nodes:
            raise ValueError(f"Loop node {name!r} requires at least one body node")

        # Build the sub-DAG
        sub = DAG(f"{name}_body")
        for n in body_nodes:
            sub.add_node(n)

        async def loop_func(ctx: Dict[str, Any]) -> Dict[str, Any]:
            iteration = 0
            while iteration < max_iterations:
                iteration += 1
                logger.info(
                    "[%s] loop iteration %d / %d", name, iteration, max_iterations
                )

                # Run the body sub-DAG。body 失败抛 DAGExecutionError——取其
                # results 继续，是否终止循环由 condition 决定
                try:
                    iter_results = await sub.run(ctx)
                except DAGExecutionError as exc:
                    iter_results = exc.results

                # Merge successful outputs into context
                for nname, nr in iter_results.items():
                    if nr.is_success:
                        ctx[nname] = nr.output

                # Evaluate loop condition
                try:
                    should_continue = condition(ctx, iteration)
                except Exception as exc:
                    logger.error(
                        "[%s] Loop condition raised %s — exiting loop",
                        name, type(exc).__name__,
                    )
                    break

                if not should_continue:
                    logger.info("[%s] loop finished after %d iteration(s)", name, iteration)
                    break
            else:
                logger.warning(
                    "[%s] max iterations (%d) reached", name, max_iterations
                )

            return ctx

        node = Node(
            name=name,
            func=loop_func,
            depends_on=depends_on or [],
            retry=retry,
            timeout=timeout,
            metadata={"loop": True, "max_iterations": max_iterations},
        )
        self.add_node(node)
        return node

    # ------------------------------------------------------------------
    # Human review support
    # ------------------------------------------------------------------

    def human_node(
        self,
        name: str,
        depends_on: Optional[List[str]] = None,
        prompt: Optional[str] = None,
        retry: Optional[RetryPolicy] = None,
        approver: Optional[ApproverFunc] = None,
    ) -> Node:
        """Register a human-in-the-loop review node.

        When the DAG reaches this node it pauses and asks a human to
        review the current context. Approving completes the node with
        the reviewed payload; rejecting raises :exc:`HumanRejected`,
        which fails the node and cascades to skip all downstream nodes
        (standard failure semantics).

        Args:
            name: Unique node name.
            depends_on: Upstream nodes whose outputs are shown for review.
            prompt: Optional extra text shown with the review request.
            retry: Optional retry policy. ``HumanRejected`` is a normal
                   exception, so a policy with default ``retry_on`` will
                   simply ask the reviewer again after rejection.
            approver: Async callable ``(node_name, payload) ->
                      {"approve": bool, "reason": Optional[str]}``.
                      Required. Use :func:`terminal_approver` for
                      interactive terminals, or your own to drive reviews
                      from elsewhere (e.g. a web UI).

        Returns:
            The registered review :class:`Node`.

        Raises:
            ValueError: If *approver* is not provided.
        """
        if approver is None:
            raise ValueError(
                f"Human node {name!r} requires an approver — "
                "pass approver=..., e.g. terminal_approver"
            )

        async def review_func(ctx: Dict[str, Any]) -> Dict[str, Any]:
            # Snapshot of everything available for review at this point.
            payload = {k: v for k, v in ctx.items() if k != name}

            print(f"\n  [REVIEW] node {name!r} is waiting for human approval")
            if prompt:
                print(f"  {prompt}")

            decision = await approver(name, payload)
            if decision.get("approve"):
                logger.info("[%s] approved by human reviewer", name)
                return {
                    "approved": True,
                    "payload": payload,
                    "reason": decision.get("reason"),
                    "approved_at": datetime.now().isoformat(timespec="seconds"),
                }

            reason = decision.get("reason") or "Rejected by human reviewer"
            logger.warning("[%s] REJECTED by human reviewer: %s", name, reason)
            raise HumanRejected(reason)

        node = Node(
            name=name,
            func=review_func,
            depends_on=depends_on or [],
            retry=retry,
            metadata={"human_review": True},
        )
        self.add_node(node)
        return node

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Validate the DAG. Returns a list of error messages (empty = valid)."""
        errors: List[str] = []

        if not self._nodes:
            errors.append("DAG has no nodes")
            return errors

        all_names = set(self._nodes)

        # Check that every dependency references an existing node
        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in all_names:
                    errors.append(
                        f"Node {node.name!r} depends on {dep!r}, "
                        f"which does not exist in the DAG"
                    )

        # Cycle detection (only if no missing-dependency errors)
        if not errors:
            cycle = self._find_cycle()
            if cycle:
                errors.append(f"Cycle detected: {' → '.join(cycle)}")

        return errors

    def _find_cycle(self) -> Optional[List[str]]:
        """DFS-based cycle detection using path stack slicing."""
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: Dict[str, int] = {n: WHITE for n in self._nodes}
        path_stack: List[str] = []

        def dfs(node_name: str) -> Optional[List[str]]:
            colour[node_name] = GRAY
            path_stack.append(node_name)  # 入栈

            for dep in self._nodes[node_name].depends_on:
                if colour[dep] == GRAY:
                    # 发现环：从 dep 第一次出现的位置直接切片提取完整环路径
                    cycle_start_idx = path_stack.index(dep)
                    return path_stack[cycle_start_idx:]

                if colour[dep] == WHITE:
                    cycle = dfs(dep)
                    if cycle:
                        return cycle

            colour[node_name] = BLACK
            path_stack.pop()  # 出栈（回溯）
            return None

        for name in self._nodes:
            if colour[name] == WHITE:
                cycle = dfs(name)
                if cycle:
                    return cycle
        return None

    # ------------------------------------------------------------------
    # Topological order (informational)
    # ------------------------------------------------------------------

    def topological_order(self) -> List[str]:
        """Return node names in topological order (Kahn's algorithm).

        Useful for visualising the execution plan.
        """
        in_degree: Dict[str, int] = {n: 0 for n in self._nodes}
        dependents: Dict[str, List[str]] = {n: [] for n in self._nodes}

        for node in self._nodes.values():
            for dep in node.depends_on:
                dependents[dep].append(node.name)
                in_degree[node.name] += 1

        queue = [n for n, d in in_degree.items() if d == 0]
        order: List[str] = []

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
        inputs: Optional[Dict[str, Any]] = None,
        concurrency: Optional[int] = None,
        on_event: Optional[Callable[[NodeResult], None]] = None,
        resume: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, NodeResult]:
        """Execute the DAG.

        Args:
            inputs: Initial data placed into the shared context before
                    execution (defaults to ``default_inputs``).
            concurrency: Maximum number of nodes running at once
                         (``None`` = unlimited).
            on_event: Optional callback invoked on every node state change
                      (running/retrying/completed/failed/skipped/cancelled).
                      Useful for live progress monitoring (e.g. a web UI).
            resume: 重启恢复用的节点快照（见 DAGExecutor.execute）。

        Returns:
            A dict mapping every node name to its :class:`NodeResult`.

        Raises:
            ValueError: If the DAG fails validation.
            DAGExecutionError: If any nodes failed.
        """
        errors = self.validate()
        if errors:
            raise ValueError(
                f"DAG {self.name!r} validation failed:\n  " + "\n  ".join(errors)
            )

        if inputs is None:
            inputs = self.default_inputs

        logger.info("== DAG %r starting (%d nodes) ==", self.name, len(self._nodes))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Topological order: %s", " -> ".join(self.topological_order()))

        executor = DAGExecutor(concurrency=concurrency, on_event=on_event)
        return await executor.execute(self._nodes, inputs, resume=resume)

    # ------------------------------------------------------------------
    # Visualisation
    # ------------------------------------------------------------------

    def to_mermaid(self) -> str:
        """Render the DAG as a Mermaid flowchart (for docs / debugging)."""
        lines = ["graph TD"]
        for node in self._nodes.values():
            nid = node.name.replace(" ", "_").replace("-", "_")
            extras = []
            if node.condition:
                extras.append("[?]")
            if (node.retry and node.retry.max_retries):
                extras.append(f"[R{node.retry.max_retries}]")
            label = node.name
            if extras:
                label += f"<br/><i>{' '.join(extras)}</i>"
            lines.append(f'    {nid}["{label}"]')
            for dep in node.depends_on:
                did = dep.replace(" ", "_").replace("-", "_")
                lines.append(f"    {did} --> {nid}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def nodes(self) -> Dict[str, Node]:
        """Return all registered nodes (read-only view)."""
        return dict(self._nodes)

    @property
    def node_names(self) -> List[str]:
        """Return the list of registered node names."""
        return list(self._nodes)

    def __repr__(self) -> str:
        return f"DAG({self.name!r}, nodes={list(self._nodes)})"

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, name: str) -> bool:
        return name in self._nodes
