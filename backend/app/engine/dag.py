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
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from app.registry import FuncDef

from .executor import DAGExecutor
from .node import ApproverFunc, ConditionFunc, Node, NodeFunc, make_func_def, wired_ctx
from .schema import PipelineConfig
from .types import DAGExecutionError, NodeResult, NodeStatus, RetryPolicy

logger = logging.getLogger(__name__)


def validate_inputs(
    inputs: dict[str, Any] | None,
    declared: dict[str, dict[str, Any]],
) -> list[str]:
    """运行时输入校验：输入键 ⊆ 声明键 + 必填缺失/为空 + validate 表达式。"""
    errors: list[str] = []
    invalid = sorted(set(inputs or {}) - set(declared))
    if invalid:
        errors.append(f"未声明的参数键: {', '.join(invalid)}")

    missing: list[str] = []
    for name, spec in declared.items():
        if not isinstance(spec, dict) or not spec.get("required"):
            continue
        value = (inputs or {}).get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
    if missing:
        errors.append(f"必填参数缺失或为空: {', '.join(missing)}")

    # validate 表达式校验
    from .condition import eval_condition
    for name, spec in declared.items():
        if not isinstance(spec, dict) or not spec.get("validate"):
            continue
        value = (inputs or {}).get(name)
        if value is None:
            continue
        view = {"value": value, name: value}
        try:
            if not eval_condition(spec["validate"], view):
                errors.append(f"参数 {name!r} 校验失败: {spec['validate']}")
        except Exception as exc:
            errors.append(f"参数 {name!r} 的 validate 表达式求值出错: {exc}")

    return errors


async def terminal_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Interactive approver: ask the reviewer on the terminal (y/n).

    Pass explicitly to ``dag.run(approver=...)``
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
        inputs: 运行时输入参数声明 ``{name: {default, required, ...}}``
                （YAML 顶层 ``inputs`` 原文）。默认值/必填键不单独拆开传，
                由 :attr:`default_inputs` / :attr:`required_inputs` 按需派生。
                若未传入但存在 ``__start__`` 节点，则从该节点 inputs 兜底。
    """

    def __init__(
        self,
        name: str = "dag",
        inputs: dict[str, dict[str, Any]] | None = None,
        output: dict[str, str] | None = None,
    ):
        self.name = name
        self._inputs = inputs if inputs else {}
        self._output = output
        self._nodes: dict[str, Node] = {}

    @property
    def inputs(self) -> dict[str, dict[str, Any]]:
        """输入参数声明 — 优先构造参数，兜底 ``__start__`` 节点 inputs。"""
        if self._inputs:
            return self._inputs
        start = self._nodes.get("__start__")
        return start.inputs if start else {}

    @property
    def default_inputs(self) -> dict[str, Any]:
        """声明了 ``default`` 的参数 → 默认值（必填键也不例外）。
        """
        return {
            name: spec["default"]
            for name, spec in self.inputs.items()
            if "default" in spec
        }

    @property
    def required_inputs(self) -> list[str]:
        """``required: true`` 的参数键（run 前缺失即 ``ValueError``）。"""
        return [name for name, spec in self.inputs.items() if spec.get("required")]

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
                    func_def=make_func_def(func),
                    name=name,
                    label=name,
                    depends_on=depends_on or [],
                    retry=retry,
                    timeout=timeout,
                    condition=condition,
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
                       层写 ``condition: $iteration < 3`` 即可）。
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
                # body 已在注册期校验过，且外层上下文对无 inputs 的 body
                # 不是「输入」，不走 run() 的输入白名单。body 失败时已完成
                # 节点的输出已在共享上下文，是否终止循环由 condition 决定
                try:
                    await DAGExecutor(nodes=sub.nodes, ctx=ctx).execute()
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

        # 类型身份绑在函数上（同注册装饰器 @node 的做法，但 loop_func 是
        # 每次调用的闭包，不进全局 REGISTRY）—— to_mermaid 经 node_type 派生读取
        loop_def = FuncDef(name="loop", func=loop_func, label="循环", description="循环执行 body 子图直至条件不满足")

        node = Node(
            func_def=loop_def,
            name=name,
            depends_on=depends_on or [],
            retry=retry,
            timeout=timeout,
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

        # inputs 参数键与节点名冲突
        if self.inputs:
            clash = sorted(set(self.inputs) & set(self._nodes))
            if clash:
                errors.append(f"输入参数键与节点名冲突: {', '.join(clash)}")

        edges = {name: node.depends_on for name, node in self._nodes.items()}
        errors.extend(PipelineConfig._validate_graph(edges))
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
        *,
        on_event: Callable[[NodeResult], Awaitable[None]] | None = None,
        approver: ApproverFunc | None = None,
        concurrency: int | None = None,
        resume: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, NodeResult], dict[str, Any] | None]:
        """Execute the DAG.

        Args:
            inputs: Initial data placed into the shared context before
                    execution (defaults to ``default_inputs``).
            on_event: 每个节点状态变化时的回调（running/retrying/completed/
                      failed/skipped/upstream_skipped/cancelled）。
            approver: 人工审核回调（human 节点挂起时调用）。
            concurrency: Maximum number of nodes running at once
                         (``None`` = unlimited).
            resume: 重启恢复用的节点快照（见 DAGExecutor.execute）。

        Returns:
            ``(results, output)`` — 映射每个节点名到 :class:`NodeResult`；
            若声明了 ``output`` 则第二项为按 ``{key: $.路径}`` 从运行文档
            提取的映射（被跳过分支 / 缺失路径的键缺席），否则 ``None``。

        Raises:
            ValueError: 结构校验失败，或（声明了 inputs 时）输入不合法。
            DAGExecutionError: If any nodes failed.
        """
        # output 参数 → __end__ 节点（程序化构建时注入，声明式已在 schema 合并）
        if self._output and "__end__" not in self._nodes:
            from app.registry import REGISTRY
            deps = []
            for ref in self._output.values():
                if isinstance(ref, str) and ref.startswith("$"):
                    root = ref[1:].split(".")[0]
                    if root in self._nodes:
                        deps.append(root)
            self.add_node(
                Node(
                    func_def=REGISTRY["end"],
                    name="__end__",
                    label="📤 输出",
                    inputs=self._output,
                    depends_on=deps,
                )
            )

        errors = self.validate()
        if errors:
            raise ValueError("DAG 结构无效:\n  " + "\n  ".join(errors))

        if inputs is None:
            inputs = self.default_inputs

        if self.inputs and (errors := validate_inputs(inputs, self.inputs)):
            raise ValueError("\n".join(errors))

        logger.info("== DAG %r starting (%d nodes) ==", self.name, len(self._nodes))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Topological order: %s", " -> ".join(self.topological_order()))

        # ctx 初始化：默认值 + 用户输入 + approver
        ctx: dict[str, Any] = {**self.default_inputs, **(inputs or {})}
        if approver is not None:
            ctx["_approver"] = approver

        executor = DAGExecutor(nodes=self._nodes, ctx=ctx, concurrency=concurrency, on_event=on_event)
        results = await executor.execute(resume=resume)

        # 输出从 __end__ 节点提取
        output = None
        output_result = results.get("__end__")
        if output_result is not None and output_result.output:
            output = output_result.output

        return results, output

    # ------------------------------------------------------------------
    # Visualisation
    # ------------------------------------------------------------------

    def to_mermaid(self) -> str:
        """Render the DAG as a Mermaid flowchart (for docs / debugging).

        inputs/output 是 DAG 中的虚拟节点，与普通节点统一渲染。
        """
        lines = ["graph TD"]
        for node in self._nodes.values():
            nid = node.name.replace(" ", "_").replace("-", "_")
            main_text = node.label or node.name

            small = []
            type_label = node.func_def.label if node.func_def else None
            if type_label:
                small.append(type_label)
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
            if node.func_def is not None and node.func_def.name == "human"
        ]

    def __repr__(self) -> str:
        return f"DAG({self.name!r}, nodes={list(self._nodes)})"

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, name: str) -> bool:
        return name in self._nodes
