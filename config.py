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

    from dag_flow import load_dag, terminal_approver

    dag = load_dag("pipeline.yaml", functions={
        "cfg_fetch": fetch_func,
        "cfg_clean": clean_func,
        "cfg_publish": publish_func,
    }, approver=terminal_approver)
    results = await dag.run()   # uses dag.default_inputs from the YAML

Node spec fields
----------------
kind            ``node`` (default) | ``human`` | ``loop``
type            function key looked up in *functions* (dict or module);
                dotted paths like ``"my_nodes.fetchers.fetch_users"`` work too
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

import builtins
import importlib
from functools import lru_cache
from typing import Any, Callable, Dict, Optional

from dag import DAG
from node import ApproverFunc, Node
from schems import RetryPolicy

_MISSING = object()

#: Fields each kind accepts; anything else in a node spec raises.
_KIND_FIELDS = {
    "node": {"type", "depends_on", "retry", "timeout", "condition", "metadata"},
    "human": {"depends_on", "retry", "prompt"},
    "loop": {"depends_on", "retry", "timeout", "condition", "body", "max_iterations"},
}


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _import_attr(key: str) -> Any:
    """Resolve a dotted path by importing the longest module prefix and
    walking the remaining attributes. Returns ``_MISSING`` if unresolvable."""
    parts = key.split(".")
    for i in range(len(parts), 0, -1):
        try:
            value: Any = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        try:
            for part in parts[i:]:
                value = getattr(value, part)
        except AttributeError:
            continue
        return value
    return _MISSING


def _resolve(functions: Any, key: str, what: str = "function") -> Callable:
    """Look up *key* in a registry (dict or module) or as a dotted path."""
    if functions is not None:
        if isinstance(functions, dict):
            if key in functions:
                return functions[key]
        else:
            attr = getattr(functions, key, None)
            if attr is not None:
                return attr

    value = _import_attr(key)
    if value is not _MISSING:
        return value

    if functions is None:
        raise ValueError(
            f"Config references {what} {key!r} but no functions registry "
            f"was provided — pass functions=<dict or module> to load_dag"
        )
    raise ValueError(f"Unknown {what} {key!r} — register it in the functions dict/module")


def _is_exception(obj: Any) -> bool:
    return isinstance(obj, type) and issubclass(obj, BaseException)


@lru_cache(maxsize=None)
def _resolve_exception(name: str) -> type:
    """Resolve an exception name like ``RuntimeError`` or ``my_errors.MyError``."""
    value = getattr(builtins, name, None) or _import_attr(name)
    if _is_exception(value):
        return value
    raise ValueError(f"Unknown exception {name!r} in retry_on")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _parse_retry(spec: Any) -> Optional[RetryPolicy]:
    """Parse a retry spec: ``retry: 3`` or a RetryPolicy field mapping."""
    if spec is None:
        return None
    if isinstance(spec, int):
        return RetryPolicy(max_retries=spec)
    if not isinstance(spec, dict):
        raise ValueError(f"Invalid retry spec: {spec!r} (use an int or a mapping)")

    fields = dict(spec)
    names = fields.pop("retry_on", None)
    if names:
        if isinstance(names, str):
            names = [names]
        fields["retry_on"] = tuple(_resolve_exception(n) for n in names)
    return RetryPolicy(**fields)


def build_dag(
    config: Dict[str, Any],
    functions: Any = None,
    approver: Optional[ApproverFunc] = None,
) -> DAG:
    """Build a :class:`DAG` from a declarative config dict.

    ``kind: human`` nodes pause for review in the run's Execution
    (answered via ``resolve_review``); an optional *approver* answers them
    automatically (e.g. :func:`dag.terminal_approver`).
    """
    if not isinstance(config, dict):
        raise ValueError(f"Config must be a dict, got {type(config).__name__}")

    dag = DAG(config.get("name", "dag"), default_inputs=config.get("inputs"))

    for name, spec in (config.get("nodes") or {}).items():
        if not isinstance(spec, dict):
            raise ValueError(
                f"Node {name!r}: spec must be a mapping, got {type(spec).__name__}"
            )

        kind = spec.get("kind", "node")
        allowed = _KIND_FIELDS.get(kind)
        if allowed is None:
            raise ValueError(
                f"Node {name!r}: unknown kind {kind!r} (expected node|human|loop)"
            )
        unknown = set(spec) - {"kind"} - allowed
        if unknown:
            raise ValueError(
                f"Node {name!r} ({kind}): unsupported field(s) {sorted(unknown)}"
            )

        deps = spec.get("depends_on") or []
        retry = _parse_retry(spec.get("retry"))

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
                raise ValueError(f"Loop node {name!r} requires a non-empty 'body' mapping")
            if not spec.get("condition"):
                raise ValueError(f"Loop node {name!r} requires a 'condition' function key")

            # Body nodes go through the same parsing path as top-level nodes.
            body_dag = build_dag({"nodes": body}, functions, approver=approver)
            dag.loop_node(
                name,
                body_nodes=list(body_dag.nodes.values()),
                condition=_resolve(functions, spec["condition"], f"loop {name!r} condition"),
                depends_on=deps,
                max_iterations=int(spec.get("max_iterations", 100)),
                retry=retry,
                timeout=spec.get("timeout"),
            )

        else:
            if "type" not in spec:
                raise ValueError(f"Node {name!r} requires a 'type' (function key)")
            condition = spec.get("condition")
            dag.add_node(Node(
                name=name,
                func=_resolve(functions, spec["type"], f"node {name!r} type"),
                depends_on=deps,
                retry=retry,
                timeout=spec.get("timeout"),
                condition=_resolve(functions, condition, f"node {name!r} condition")
                if condition else None,
                metadata=spec.get("metadata") or {},
            ))

    return dag


def load_dag(
    path: str,
    functions: Any = None,
    approver: Optional[ApproverFunc] = None,
) -> DAG:
    """Load a YAML file and build the DAG from it (requires PyYAML).

    *approver* is passed to every ``kind: human`` node (see
    :func:`build_dag`).
    """
    try:
        import yaml
    except ImportError as exc:
        raise ImportError("load_dag requires PyYAML — run: pip install pyyaml") from exc

    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    return build_dag(config, functions, approver=approver)
