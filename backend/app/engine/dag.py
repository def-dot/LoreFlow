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
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from .executor import DAGExecutor
from .node import ApproverFunc, ConditionFunc, HumanRejected, Node, NodeFunc
from .types import DAGExecutionError, NodeResult, NodeStatus, RetryPolicy

logger = logging.getLogger(__name__)


async def terminal_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
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


def find_cycle(edges: Mapping[str, Sequence[str]]) -> list[str] | None:
    """DFS 环检测：``edges = {节点名: 依赖名列表}``，返回环路径或 ``None``。

    DAG.validate 与 declarative 的配置层结构校验共用：依赖图中指向
    不存在节点的边直接跳过（缺失依赖由调用方单独报错，缺失不可能成环）。
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[str, int] = {n: WHITE for n in edges}
    path_stack: list[str] = []

    def dfs(node_name: str) -> list[str] | None:
        colour[node_name] = GRAY
        path_stack.append(node_name)  # 入栈

        for dep in edges[node_name]:
            if dep not in colour:
                continue
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

    for name in edges:
        if colour[name] == WHITE:
            cycle = dfs(name)
            if cycle:
                return cycle
    return None


class DAG:
    """A Directed Acyclic Graph workflow.

    Register nodes with the :meth:`node` decorator or :meth:`add_node`,
    then execute the entire graph with :meth:`run`.

    Parameters:
        name: A human-readable label for this workflow (used in logs & diagrams).
        params: 运行时输入参数声明 ``{name: {default, required, ...}}``
                （load_dag 传入 YAML ``params`` 原文）。默认值/必填键不
                单独拆开传，由 :attr:`default_inputs` / :attr:`required_inputs`
                按需派生。
        on_event: Optional async callback invoked on every node state change
                  (running/retrying/completed/failed/upstream_failed/skipped/
                  cancelled) and awaited. Useful for live progress monitoring
                  (e.g. a web UI).
    """

    def __init__(
        self,
        name: str = "dag",
        params: dict[str, dict[str, Any]] | None = None,
        on_event: Callable[[NodeResult], Awaitable[None]] | None = None,
    ):
        self.name = name
        # 声明原样保存（形状由 validate_params 保证），派生视图见下方属性
        self.params = params if params else {}
        self.on_event = on_event
        self._nodes: dict[str, Node] = {}

    @property
    def default_inputs(self) -> dict[str, Any]:
        """声明了 ``default`` 的参数 → 默认值（run 未传 inputs 时使用，
        与 ``required`` 无关：必填键的 default 在缺显式输入时顶班）。
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
            ValueError: If a node with the same name already exists.
        """
        if node.name in self._nodes:
            raise ValueError(f"节点名重复: {node.name!r}")
        self._nodes[node.name] = node
        return node

    def node(
        self,
        name: str,
        depends_on: list[str] | None = None,
        retry: RetryPolicy | None = None,
        timeout: float | None = None,
        condition: ConditionFunc | None = None,
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
        condition: Callable[[dict[str, Any], int], bool],
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
            raise ValueError(f"循环节点 {name!r} 至少需要一个 body 节点")

        # Build the sub-DAG
        sub = DAG(f"{name}_body")
        for n in body_nodes:
            sub.add_node(n)

        async def loop_func(ctx: dict[str, Any]) -> dict[str, Any]:
            iteration = 0
            while iteration < max_iterations:
                iteration += 1
                logger.info("[%s] loop iteration %d / %d", name, iteration, max_iterations)

                # Run the body sub-DAG。body 失败抛 DAGExecutionError——取其
                # results 继续，是否终止循环由 condition 决定
                try:
                    iter_results = await sub.run(ctx)
                except DAGExecutionError as exc:
                    iter_results = exc.results

                # Merge successful outputs into context
                for nname, nr in iter_results.items():
                    if nr.status == NodeStatus.COMPLETED:
                        ctx[nname] = nr.output

                # Evaluate loop condition
                try:
                    should_continue = condition(ctx, iteration)
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
            # label 供 to_mermaid 做展示主行（可被同名 key 覆盖）
            metadata={"loop": True, "max_iterations": max_iterations, "label": "循环"},
        )
        self.add_node(node)
        return node

    # ------------------------------------------------------------------
    # Human review support
    # ------------------------------------------------------------------

    def human_node(
        self,
        name: str,
        depends_on: list[str] | None = None,
        prompt: str | None = None,
        condition: ConditionFunc | None = None,
        retry: RetryPolicy | None = None,
        approver: ApproverFunc | None = None,
        review: Mapping[str, Mapping[str, str]] | Sequence[str] | None = None,
    ) -> Node:
        """Register a human-in-the-loop review node.

        When the DAG reaches this node it pauses and asks a human to
        review the current context. Approving completes the node with
        the reviewed payload; rejecting raises :exc:`HumanRejected`,
        which fails the node and cascades to skip all downstream nodes
        (standard failure semantics). The rejection details are carried
        in the FAILED node result's ``output``.

        Args:
            name: Unique node name.
            depends_on: Upstream nodes whose outputs are shown for review.
            prompt: Optional extra text shown with the review request.
            condition: Optional predicate; if it returns False the review
                       is skipped (the human is never asked).
            retry: Optional retry policy. 拒绝是终局决策（HumanRejected 被
                   executor 专用捕获，不进重试循环），retry 对拒绝无效。
            approver: Async callable ``(node_name, payload) ->
                      {"approve": bool, "reason": Optional[str]}``.
                      Required. Use :func:`terminal_approver` for
                      interactive terminals, or your own to drive reviews
                      from elsewhere (e.g. a web UI).
            review: Optional review view — raw format mapping of context key →
                    {label: display text}. Or a bare list of keys, label = key.
                    Declared: the payload shown to the reviewer contains
                    only these keys (plus a leading ``_review`` entry
                    carrying the labels; runtime-absent keys become
                    ``None``). ``None`` (default): the payload is the
                    whole context minus the node itself.

        Returns:
            The registered review :class:`Node`.

        Raises:
            ValueError: If *approver* is not provided.
        """
        if approver is None:
            raise ValueError(f"人工审核节点 {name!r} 必须提供 approver —— 例如 terminal_approver")

        # review 声明：支持原始格式 {key: {label: text}} 或简化格式 {key: label} 或键列表
        if review is None:
            review_view: dict[str, dict[str, str]] | dict[str, str] | None = None
        elif isinstance(review, Mapping):
            # 检查是否是原始格式 {key: {label: text}}
            if review and all(isinstance(v, Mapping) for v in review.values()):
                review_view = dict(review)  # 直接使用原始格式
            else:
                # 兼容简化格式 {key: label}
                review_view = {str(k): {"label": str(v)} for k, v in review.items()}
        elif isinstance(review, (list, tuple)):
            review_view = {str(k): {"label": str(k)} for k in review}
        else:
            raise ValueError(f"review 必须是 {{key: {label: text}}} 映射、{key: label} 映射或键列表，实际是 {type(review).__name__}")

        async def review_func(ctx: dict[str, Any]) -> dict[str, Any]:
            # "_" 前缀键是引擎保留的展示元数据（消费方按前缀跳过）：
            # _prompt = 作者给审核者的把关指引，_review = 声明视图的字段标签
            if review_view is not None:
                # 声明视图：审核者只看声明的键。运行时缺失的键（可选参数未提供、
                # 条件跳过的节点）显式置 None —— 卡片显示「未提供」而非静默消失。
                payload: dict[str, Any] = {}
                if prompt:
                    payload["_prompt"] = prompt

                # 提取标签映射（支持原始格式和简化格式）
                label_map: dict[str, str] = {}
                if review_view and all(isinstance(v, Mapping) for v in review_view.values()):
                    # 原始格式 {key: {label: text}}
                    label_map = {k: v["label"] for k, v in review_view.items()}
                else:
                    # 简化格式 {key: label}
                    label_map = dict(review_view)  # type: ignore

                payload["_review"] = label_map
                for key in review_view:
                    payload[key] = ctx.get(key)
            else:
                # Snapshot of everything available for review at this point.
                payload = {k: v for k, v in ctx.items() if k != name}
                if prompt:
                    payload = {"_prompt": prompt, **payload}

            print(f"\n  [REVIEW] node {name!r} is waiting for human approval")
            if prompt:
                print(f"  {prompt}")

            decision = await approver(name, payload)
            if decision.get("approve"):
                logger.info("[%s] approved by human reviewer", name)
                # 审核修订（"改了再通过"）：只覆盖 payload 已有的键，防注入
                # 新键；原始上游输出不动，修订痕迹由决策行（edits）留档
                edits = decision.get("edits")
                if isinstance(edits, dict) and edits:
                    payload = {**payload, **{k: v for k, v in edits.items() if k in payload}}
                return {
                    "approved": True,
                    "payload": payload,
                    "decision": decision,
                    "approved_at": datetime.now().isoformat(timespec="seconds"),
                }

            reason = decision.get("reason") or "被人工审核拒绝"
            logger.warning("[%s] REJECTED by human reviewer: %s", name, reason)
            raise HumanRejected(
                reason,
                output={
                    "approved": False,
                    "reason": reason,
                    "payload": payload,
                    "decision": decision,
                    "approved_at": datetime.now().isoformat(timespec="seconds"),
                },
            )

        node = Node(
            name=name,
            func=review_func,
            depends_on=depends_on or [],
            condition=condition,
            retry=retry,
            # label 供 to_mermaid 做展示主行（可被同名 key 覆盖）
            metadata={"human_review": True, "label": "人工审核"},
        )
        self.add_node(node)
        return node

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the DAG. Returns a list of error messages (empty = valid)."""
        errors: list[str] = []

        if not self._nodes:
            errors.append("DAG 没有节点")
            return errors

        all_names = set(self._nodes)

        # Check that every dependency references an existing node
        for node in self._nodes.values():
            for dep in node.depends_on:
                if dep not in all_names:
                    errors.append(f"节点 {node.name!r} 依赖的 {dep!r} 不在 DAG 中")

        # Cycle detection (only if no missing-dependency errors)
        if not errors:
            cycle = self._find_cycle()
            if cycle:
                errors.append(f"检测到循环依赖: {' → '.join(cycle)}")

        return errors

    def _find_cycle(self) -> list[str] | None:
        """委托共享的 :func:`find_cycle`（edges 视图来自已注册节点）。"""
        return find_cycle(
            {name: node.depends_on for name, node in self._nodes.items()}
        )

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
            ValueError: If the DAG fails validation or required inputs are missing.
            DAGExecutionError: If any nodes failed.
        """
        errors = self.validate()
        if errors:
            raise ValueError(f"DAG {self.name!r} 校验失败:\n  " + "\n  ".join(errors))

        if inputs is None:
            inputs = self.default_inputs

        missing = [k for k in self.required_inputs if k not in (inputs or {})]
        if missing:
            raise ValueError(f"缺少必填输入参数: {', '.join(missing)}")

        logger.info("== DAG %r starting (%d nodes) ==", self.name, len(self._nodes))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Topological order: %s", " -> ".join(self.topological_order()))

        executor = DAGExecutor(concurrency=concurrency, on_event=self.on_event)
        return await executor.execute(self._nodes, inputs, resume=resume)

    # ------------------------------------------------------------------
    # Visualisation
    # ------------------------------------------------------------------

    def to_mermaid(self) -> str:
        """Render the DAG as a Mermaid flowchart (for docs / debugging).

        节点主行是 YAML 里定义的节点名（快照/日志/API 的关联键）；小字行
        展示节点类型 —— 注册表键 + label（``metadata["type"]``/``["label"]``，
        见 load_dag），后接 [?]/[Rn] 标记。human/loop 没有 注册表类型，
        只显示 label。
        """
        lines = ["graph TD"]
        for node in self._nodes.values():
            nid = node.name.replace(" ", "_").replace("-", "_")
            small = []
            type_name = node.metadata.get("type")
            label = node.metadata.get("label")
            if type_name:
                small.append(f"{type_name} · {label}" if label else type_name)
            elif label:
                small.append(label)
            if node.condition:
                small.append("[?]")
            if node.retry and node.retry.max_retries:
                small.append(f"[R{node.retry.max_retries}]")
            text = node.name + (f"<br/><i>{' '.join(small)}</i>" if small else "")
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
        """Nodes that pause for human review (``human_review`` metadata)."""
        return [node for node in self._nodes.values() if node.metadata.get("human_review")]

    def __repr__(self) -> str:
        return f"DAG({self.name!r}, nodes={list(self._nodes)})"

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, name: str) -> bool:
        return name in self._nodes
