"""
Declarative config → DAG 构建。

校验统一在 PipelineConfig（schema.py）中完成。
"""

from __future__ import annotations

import logging
from typing import Any

from app.registry import REGISTRY

from .dag import DAG
from .node import Node
from .resolve import parse_retry
from .schema import NodeSpec, PipelineConfig

logger = logging.getLogger(__name__)



def load_dag(
    config: dict[str, Any] | PipelineConfig,
    approver: Any = None,
) -> DAG:
    """Build a :class:`DAG` from a config dict or PipelineConfig model.

    dict 传入时由 PipelineConfig 完成全部校验（结构 + 语义）。

    __start__ / __end__ 节点在 cfg.nodes 中，与用户节点统一遍历。
    """
    cfg = config if isinstance(config, PipelineConfig) else PipelineConfig(**config)

    def _start_inputs() -> dict[str, Any]:
        start = cfg.nodes.get("__start__")
        if not start or not start.inputs:
            return {}
        return {k: v.model_dump() if hasattr(v, "model_dump") else v
                for k, v in start.inputs.items()}

    dag = DAG(cfg.name or "dag", inputs=_start_inputs())

    # input_names 用于自动注入 __start__ 依赖
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