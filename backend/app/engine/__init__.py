"""
DAG Flow engine — async DAG-based workflow orchestration.

Quick start::

    from app.engine import Pipeline, RetryPolicy

    pipeline = Pipeline.model_validate({
        "name": "pipeline",
        "nodes": [
            {"name": "fetch", "type": "my_fetch", "retry": 3},
            {"name": "process", "type": "my_process", "depends_on": ["fetch"]},
        ],
    })

    results, _ = await pipeline.run()
"""

from .pipeline import Node, Pipeline, RetryPolicy, terminal_approver
from .validator import validate_dag, validate_ref
from .types import (
    PipeLineExecutionError,
    HumanRejected,
    NodeResult,
    NodeStatus,
    SuspendExecution,
    wired_ctx,
)

__all__ = [
    "PipeLineExecutionError",
    "HumanRejected",
    "Node",
    "NodeResult",
    "NodeStatus",
    "Pipeline",
    "RetryPolicy",
    "SuspendExecution",
    "terminal_approver",
    "validate_dag",
    "validate_ref",
    "wired_ctx",
]
