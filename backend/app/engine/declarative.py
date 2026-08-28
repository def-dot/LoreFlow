"""
Declarative config → DAG 构建（校验全部在 app.engine.validate）。
"""

from __future__ import annotations

import yaml
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.registry import REGISTRY

from . import validate
from .condition import compile_condition
from .dag import DAG
from .node import ApproverFunc, ConditionFunc, Node
from .resolve import parse_retry
from .types import NodeEventFunc


def _wired_view(ctx: dict[str, Any], wiring: dict[str, Any]) -> dict[str, Any]:
    """接线 → 节点视图 ``{**ctx, **{本地键: 视图值}}``。
    """
    def resolve(v: Any) -> Any:
        if isinstance(v, str) and v.startswith("$"):
            value = ctx
            for part in v[1:].split("."):
                if not isinstance(value, Mapping) or part not in value:
                    return None
                value = value[part]
            return value
        return v  # 字面量原样透传（含 dict/list；嵌套 $ 引用由 validate 载入期拒绝）

    return {**ctx, **{k: resolve(v) for k, v in wiring.items()}}


def _condition_func(expr: str | bool, wiring: dict[str, Any] | None = None) -> ConditionFunc:
    """条件（表达式字符串或 true/false 常量，+ 可选接线）→ 可调用谓词。
    """
    if isinstance(expr, bool):
        return lambda ctx: expr  # 常量开关：true = 恒执行 / false = 恒跳过

    cond = compile_condition(expr)

    if not wiring:
        return cond

    def wired(ctx: dict[str, Any]) -> bool:
        return cond(_wired_view(ctx, wiring))

    return wired


def _wired_func(
    func: Callable[..., Any],
    wiring: dict[str, Any] | None = None,
    deps: list[str] | None = None,
) -> Callable[..., Any]:
    """数据流接线视图套在节点函数外（视图语义见 _wired_view；工厂按参捕获，
    循环内多次调用无闭包晚绑定）。
    """
    async def wired(ctx: dict[str, Any]) -> Any:
        view = _wired_view(ctx, wiring) if wiring else dict(ctx)
        if deps:
            view["_upstream"] = {d: ctx.get(d) for d in deps}
        return await func(view)
    return wired


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
        # YAML 顶层 ``inputs``（调用方表单声明）→ 引擎内统一叫 params（声明）
        params=config.get("inputs") or {},
        on_event=on_event,
    )

    for name, spec in config["nodes"].items():
        type_key = spec.get("type")

        deps = spec.get("depends_on") or []
        retry = parse_retry(spec.get("retry"))

        condition = spec.get("condition")
        wiring = spec.get("inputs")
        cond_func = _condition_func(condition, wiring) if condition is not None else None

        if type_key == "human":
            human = dag.human_node(
                name,
                depends_on=deps,
                prompt=spec.get("prompt"),
                condition=cond_func,
                retry=retry,
                approver=approver,
                review=spec.get("review"),
            )
            if spec.get("label"):
                human.metadata["label"] = spec["label"]

        elif type_key == "loop":
            body = spec.get("body")

            # Body nodes go through the same parsing path as top-level nodes.
            body_dag = load_dag({"nodes": body}, approver=approver)
            loop = dag.loop_node(
                name,
                body_nodes=list(body_dag.nodes.values()),
                condition=cond_func,
                depends_on=deps,
                max_iterations=int(spec.get("max_iterations", 100)),
                retry=retry,
                timeout=spec.get("timeout"),
            )
            if spec.get("label"):
                loop.metadata["label"] = spec["label"]

        else:
            nt = REGISTRY[type_key]
            func = _wired_func(nt.func, wiring, deps)
            node_label = spec.get("label") or nt.label
            dag.add_node(
                Node(
                    name=name,
                    func=func,
                    node_type=nt,
                    label=node_label,
                    inputs=spec.get("inputs"),
                    depends_on=deps,
                    retry=retry,
                    timeout=spec.get("timeout"),
                    condition=cond_func,
                    metadata={
                        "type": nt.name,
                        **(spec.get("metadata") or {}),
                    },
                )
            )
    return dag
