"""
DAG Flow engine — async DAG-based workflow orchestration.

Quick start::

    from app.engine import Pipeline, RetryPolicy

    pipeline = Pipeline(**{
        "name": "pipeline",
        "nodes": [
            {"name": "fetch", "type": "my_fetch", "retry": 3},
            {"name": "process", "type": "my_process", "depends_on": ["fetch"]},
        ],
    })

    results, _ = await pipeline.run()
"""

from app.registry.funcs.human import human

from .pipeline import Node, Pipeline, RetryPolicy, terminal_approver
from .node import HumanRejected, wired_ctx
from .validator import validate_inputs
from .types import (
    DAGExecutionError,
    NodeResult,
    NodeStatus,
    SuspendExecution,
)

__all__ = [
    "DAGExecutionError",
    "HumanRejected",
    "Node",
    "NodeResult",
    "NodeStatus",
    "Pipeline",
    "RetryPolicy",
    "SuspendExecution",
    "human",
    "terminal_approver",
    "validate_inputs",
    "wired_ctx",
]
