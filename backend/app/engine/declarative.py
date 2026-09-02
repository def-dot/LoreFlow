"""
Declarative config → DAG 构建（校验全部在 app.engine.validate）。
"""

from __future__ import annotations

from typing import Any

from app.registry import REGISTRY

from . import validate
from .dag import DAG
from .node import ApproverFunc, Node
from .resolve import parse_retry
from .types import NodeEventFunc


def load_dag(
    config: dict[str, Any],
    approver: ApproverFunc | None = None,
    on_event: NodeEventFunc | None = None,
) -> DAG:
    """Build a :class:`DAG` from a config dict."""

    errors = validate.validate_config(config)
    if errors:
        raise ValueError("DAG 配置无效:\n  " + "\n  ".join(errors))

    dag = DAG(
        config.get("name", "dag"),
        params=config.get("inputs") or {},
        on_event=on_event,
        approver=approver,
    )

    for name, spec in config["nodes"].items():
        deps = spec.get("depends_on") or []
        retry = parse_retry(spec.get("retry"))

        node_type = REGISTRY[spec["type"]]

        dag.add_node(
            Node(
                name=name,
                func=node_type.func,
                label=spec.get("label"),
                description=spec.get("description"),
                inputs=spec.get("inputs"),
                depends_on=deps,
                retry=retry,
                timeout=spec.get("timeout"),
                condition=spec.get("condition"),
            )
        )
    return dag
