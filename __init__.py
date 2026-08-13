"""
DAG Flow — Async DAG-based workflow orchestration engine.

Supports:
- **Serial** execution: dependency chains (A → B → C)
- **Parallel** execution: independent nodes run concurrently
- **Branching**: conditional node execution via ``condition`` predicates
- **Looping**: iterate a sub-DAG until a condition is met
- **Retry**: configurable exponential backoff with jitter
- **Timeout**: per-node execution deadline

Quick start::

    from dag_flow import DAG, RetryPolicy

    dag = DAG("pipeline")

    @dag.node("fetch", retry=RetryPolicy(max_retries=3))
    async def fetch(ctx):
        return await api.get("/data")

    @dag.node("process", depends_on=["fetch"])
    async def process(ctx):
        return transform(ctx["fetch"])

    results = await dag.run()
    print(results["process"].output)
"""

from .config import build_dag, load_dag
from .dag import DAG
from .executor import DAGExecutionError
from .node import HumanRejected, Node
from .schems import NodeResult, NodeStatus, RetryPolicy

__all__ = [
    "DAG",
    "DAGExecutionError",
    "HumanRejected",
    "Node",
    "NodeResult",
    "NodeStatus",
    "RetryPolicy",
    "build_dag",
    "load_dag",
]
