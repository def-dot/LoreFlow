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


def _inject_input_deps(node_name: str, spec: NodeSpec, input_names: set[str]) -> list[str]:
    """扫描节点 inputs + condition 中的 $引用，若引用了 pipeline inputs 则注入 __start__ 依赖。"""

    def scan(obj: Any) -> bool:
        if isinstance(obj, str) and obj.startswith("$"):
            root = obj[1:].split(".")[0]
            return root in input_names
        elif isinstance(obj, dict):
            return any(scan(v) for v in obj.values())
        elif isinstance(obj, list):
            return any(scan(v) for v in obj)
        return False

    if scan(spec.inputs) or (isinstance(spec.condition, str) and scan(spec.condition)):
        return ["__start__"]
    return []


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
    start_node = cfg.nodes.get("__start__")
    input_names = set(start_node.inputs) if start_node and start_node.inputs else set()

    for name, spec in cfg.nodes.items():
        node_type = REGISTRY[spec.type]

        # 自动注入 __start__ 依赖
        extra_deps = _inject_input_deps(name, spec, input_names)

        dag.add_node(
            Node(
                func_def=node_type,
                name=name,
                label=spec.label or "",
                description=spec.description,
                inputs=spec.inputs,
                depends_on=spec.depends_on + extra_deps,
                retry=parse_retry(spec.retry),
                timeout=spec.timeout,
                condition=spec.condition,
            )
        )

    return dag