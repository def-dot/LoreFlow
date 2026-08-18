"""
Declarative configuration layer for DAG Flow.

Define a workflow's *wiring* in YAML/JSON instead of Python code.
Only the node functions (the actual work) need to be implemented and
registered — the orchestration itself (dependencies, retries,
conditions, human review, loops) lives in the config file.

Example YAML::

    name: content_pipeline

    nodes:
      fetch:
        type: cfg_fetch        # function key in the registry
        retry: 2               # shorthand for RetryPolicy(max_retries=2)

      clean:
        type: cfg_clean
        depends_on: [fetch]

      review:
        kind: human            # human-in-the-loop review node
        depends_on: [clean]
        prompt: "Please check the result."

      publish:
        type: cfg_publish
        depends_on: [review]

Usage::

    from app.engine import load_dag, terminal_approver

    # ``type``/``condition`` 键必须是 app.registry 中注册过的名字:
    dag = load_dag("pipeline.yaml", approver=terminal_approver)
    results = await dag.run()   # uses dag.default_inputs from the YAML

Node spec fields
----------------
kind            ``node`` (default) | ``human`` | ``loop``
type            function key registered in *app.registry*;
depends_on      list of upstream node names
retry           int (max_retries shorthand) or a RetryPolicy field mapping:
                max_retries, backoff_base, backoff_factor, backoff_max,
                retry_on (list of exception names), jitter
timeout         per-node timeout in seconds
condition       function key for a predicate: ``(ctx) -> bool`` for node
                branching; ``(ctx, iteration) -> bool`` for loops, where
                True means "keep looping"
prompt          (kind: human) extra text shown to the reviewer
body            (kind: loop) mapping of body nodes, same schema as ``nodes``
max_iterations  (kind: loop) safety cap, default 100
metadata        (kind: node) arbitrary key-value pairs

Top-level fields
----------------
name            DAG name (default ``"dag"``)
inputs          initial context passed to :meth:`DAG.run`; stored on the
                returned DAG as ``dag.default_inputs``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.registry import resolve_function

from .dag import DAG
from .node import ApproverFunc, Node, NodeEventFunc
from .resolve import parse_retry

#: Fields each kind accepts; anything else in a node spec raises.
_KIND_FIELDS = {
    "node": {"type", "depends_on", "retry", "timeout", "condition", "metadata"},
    "human": {"depends_on", "retry", "prompt"},
    "loop": {"depends_on", "retry", "timeout", "condition", "body", "max_iterations"},
}


def _load_yaml(path: str | Path) -> Any:
    """Read and parse a YAML config file (requires PyYAML)."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("从文件加载 DAG 需要 PyYAML —— 请运行: pip install pyyaml") from exc
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except OSError as exc:
        raise ValueError(f"无法读取配置文件 {path!r}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"配置文件 {path!r} 的 YAML 无效: {exc}") from exc


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
        config = _load_yaml(source)
    else:
        raise ValueError(f"配置必须是 dict 或文件路径，实际是 {type(source).__name__}")

    dag = DAG(
        config.get("name", "dag"),
        default_inputs=config.get("inputs"),
        on_event=on_event,
    )

    for name, spec in (config.get("nodes") or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(f"节点 {name!r}: 定义必须是映射(dict)，实际是 {type(spec).__name__}")

        kind = spec.get("kind", "node")
        allowed = _KIND_FIELDS.get(kind)
        if allowed is None:
            raise ValueError(f"节点 {name!r}: 未知类型 {kind!r}（支持 node|human|loop）")
        unknown = set(spec) - {"kind"} - allowed
        if unknown:
            raise ValueError(f"节点 {name!r}（{kind}）: 不支持的字段 {sorted(unknown)}")

        deps = spec.get("depends_on") or []
        retry = parse_retry(spec.get("retry"))

        if kind == "human":
            dag.human_node(
                name,
                depends_on=deps,
                prompt=spec.get("prompt"),
                retry=retry,
                approver=approver,
            )

        elif kind == "loop":
            body = spec.get("body")
            if not isinstance(body, dict) or not body:
                raise ValueError(f"循环节点 {name!r} 需要非空的 'body' 映射")
            if not spec.get("condition"):
                raise ValueError(f"循环节点 {name!r} 需要 'condition' 函数键")

            # Body nodes go through the same parsing path as top-level nodes.
            body_dag = load_dag({"nodes": body}, approver=approver)
            dag.loop_node(
                name,
                body_nodes=list(body_dag.nodes.values()),
                condition=resolve_function(spec["condition"], f"循环 {name!r} 的条件"),
                depends_on=deps,
                max_iterations=int(spec.get("max_iterations", 100)),
                retry=retry,
                timeout=spec.get("timeout"),
            )

        else:
            if "type" not in spec:
                raise ValueError(f"节点 {name!r} 需要 'type'（函数键）")
            condition = spec.get("condition")
            dag.add_node(
                Node(
                    name=name,
                    func=resolve_function(spec["type"], f"节点 {name!r} 的类型"),
                    depends_on=deps,
                    retry=retry,
                    timeout=spec.get("timeout"),
                    condition=resolve_function(condition, f"节点 {name!r} 的条件") if condition else None,
                    metadata=spec.get("metadata") or {},
                )
            )

    errors = dag.validate()
    if errors:
        raise ValueError("DAG 配置无效:\n  " + "\n  ".join(errors))
    return dag
