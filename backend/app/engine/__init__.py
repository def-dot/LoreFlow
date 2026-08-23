"""
DAG Flow engine — async DAG-based workflow orchestration.

Supports:
- **Serial** execution: dependency chains (A → B → C)
- **Parallel** execution: independent nodes run concurrently
- **Branching**: conditional node execution via ``condition`` predicates
- **Looping**: iterate a sub-DAG until a condition is met
- **Retry**: configurable exponential backoff with jitter
- **Timeout**: per-node execution deadline
- **Human review**: human-in-the-loop approval nodes
- **Declarative config**: build DAGs from YAML/JSON

Quick start::

    from app.engine import DAG, RetryPolicy

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

from .dag import DAG, terminal_approver
from .declarative import load_dag
from .node import HumanRejected, Node
from .types import (
    DAGExecutionError,
    NodeResult,
    NodeStatus,
    RetryPolicy,
    SuspendExecution,
    SKIP_CONDITION,
    SKIP_UPSTREAM_FAILED,
)

__all__ = [
    "DAG",
    "DAGExecutionError",
    "HumanRejected",
    "Node",
    "NodeResult",
    "NodeStatus",
    "RetryPolicy",
    "SKIP_CONDITION",
    "SKIP_UPSTREAM_FAILED",
    "SuspendExecution",
    "load_dag",
    "terminal_approver",
]
