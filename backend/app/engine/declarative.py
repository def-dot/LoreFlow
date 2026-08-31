"""
Declarative config → DAG 构建（校验全部在 app.engine.validate）。
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Any

from app.registry import REGISTRY

from . import validate
from .dag import DAG
from .node import ApproverFunc, Node
from .resolve import parse_retry
from .types import NodeEventFunc


def read_yaml(path: str | Path) -> tuple[str, Any]:
    """Read a YAML config file, returning ``(raw_text, config)`` (requires PyYAML).

    ``config`` is always a mapping: empty YAML becomes ``{}``, and a
    non-mapping top level raises ``ValueError``, as do read/parse errors.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取配置文件 {path!r}: {exc}") from exc
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"配置文件 {path!r} 的 YAML 无效: {exc}") from exc
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("顶层必须是映射(dict)")
    return raw, config


def load_dag(
    source: str | Path | dict[str, Any],
    approver: ApproverFunc | None = None,
    on_event: NodeEventFunc | None = None,
) -> DAG:
    """Build a :class:`DAG` from a config dict or a YAML/JSON file path.
    """
    if isinstance(source, dict):
        config = source
    elif isinstance(source, (str, Path)):
        _, config = read_yaml(source)
    else:
        raise ValueError(f"配置必须是 dict 或文件路径，实际是 {type(source).__name__}")

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
                inputs=spec.get("inputs"),
                depends_on=deps,
                retry=retry,
                timeout=spec.get("timeout"),
                condition=spec.get("condition"),
            )
        )
    return dag
