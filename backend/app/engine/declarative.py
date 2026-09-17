"""
Declarative config → DAG 构建。

校验统一在 PipelineConfig（schema.py）中完成。
"""

from __future__ import annotations

from typing import Any

from app.registry import REGISTRY

from .dag import DAG
from .node import Node
from .resolve import parse_retry
from .schema import PipelineConfig


def load_dag(config: dict[str, Any] | PipelineConfig) -> DAG:
    """Build a :class:`DAG` from a config dict or PipelineConfig model.

    dict 传入时由 PipelineConfig 完成全部校验（结构 + 语义）。
    """
    cfg = config if isinstance(config, PipelineConfig) else PipelineConfig(**config)

    dag = DAG(
        cfg.name or "dag",
        inputs={k: v.model_dump() for k, v in cfg.inputs.items()} if cfg.inputs else {},
        output=cfg.output,
    )

    for name, spec in cfg.nodes.items():
        node_type = REGISTRY[spec.type]

        dag.add_node(
            Node(
                func_def=node_type,
                name=name,
                label=spec.label or "",
                description=spec.description,
                inputs=spec.inputs,
                depends_on=spec.depends_on,
                retry=parse_retry(spec.retry),
                timeout=spec.timeout,
                condition=spec.condition,
            )
        )
    return dag
