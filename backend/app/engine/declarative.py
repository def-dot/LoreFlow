"""
Declarative config → DAG 构建。

校验统一在 PipelineConfig（schema.py）中完成。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import create_model

from app.registry import REGISTRY, FuncDef
from app.core.config import settings

from .dag import DAG
from .node import Node, wired_ctx
from .resolve import parse_retry
from .schema import PipelineConfig

logger = logging.getLogger(__name__)

PIPELINES_DIR = settings.PIPELINES_DIR


def make_pipeline_func(
    pipeline_name: str,
    cfg: PipelineConfig,
    output_mapping: dict[str, str] | None,
    loading: set[str],
) -> tuple[Any, FuncDef]:
    """为子 pipeline 创建执行函数 + FuncDef。

    Args:
        pipeline_name: 子 pipeline 名称（对应 YAML 文件名）。
        cfg: 子 pipeline 的 PipelineConfig。
        output_mapping: 子 pipeline output → 节点输出的映射。
        loading: 循环检测栈（当前正在加载的 pipeline 名）。

    Returns:
        ``(func, func_def)`` 二元组。
    """

    loading_snapshot = set(loading)

    async def pipeline_func(**kwargs: Any) -> dict[str, Any]:
        sub_dag = load_dag(cfg, _loading=set(loading_snapshot))
        approver = kwargs.pop("_approver", None)

        results, output = await sub_dag.run(inputs=kwargs, approver=approver)

        if output_mapping and output:
            view = {
                **output,
                **{k: v.output for k, v in results.items() if v.output is not None},
            }
            return wired_ctx(view, output_mapping)
        return output or {}

    # 动态构建 output_schema（让父 DAG 校验 $node.field 引用）
    output_schema = None
    if output_mapping:
        clean_fields: dict[str, Any] = {}
        for k, v in output_mapping.items():
            field_name = v.lstrip("$") if isinstance(v, str) and v.startswith("$") else k
            clean_fields[field_name] = (Any, None)
        if clean_fields:
            output_schema = create_model(f"{pipeline_name}Output", **clean_fields)

    func_def = FuncDef(
        name=pipeline_name,
        func=pipeline_func,
        label=cfg.name or pipeline_name,
        description=cfg.description or "",
        metadata={"pipeline_config": cfg, "output_mapping": output_mapping},
        output_schema=output_schema,
        accepts_extra=True,
    )
    return pipeline_func, func_def





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
    *,
    _loading: set[str] | None = None,
) -> DAG:
    """Build a :class:`DAG` from a config dict or PipelineConfig model.

    dict 传入时由 PipelineConfig 完成全部校验（结构 + 语义）。

    inputs 汇聚为单一 ``__start__`` 虚拟节点，output 注入 ``__end__`` 虚拟节点。

    Args:
        _loading: 循环检测栈（内部递归用）。
    """
    _loading = _loading if _loading is not None else set()
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
        if spec.type == "pipeline":
            if not spec.pipeline:
                raise ValueError(f"节点 {name!r}: pipeline 类型必须指定 'pipeline' 字段")
            if spec.pipeline in _loading:
                chain = " → ".join(_loading) + f" → {spec.pipeline}"
                raise ValueError(f"循环引用: {chain}")
            _loading.add(spec.pipeline)
            try:
                sub_cfg = _load_pipeline_config(spec.pipeline)
                _, func_def = make_pipeline_func(spec.pipeline, sub_cfg, spec.output_mapping, _loading)
            finally:
                _loading.discard(spec.pipeline)
            node_type = func_def
        else:
            node_type = REGISTRY[spec.type]

        # 自动注入 __input__ 依赖
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


def _load_pipeline_config(name: str) -> PipelineConfig:
    """从 PIPELINES_DIR 加载子 pipeline 配置。"""
    import yaml

    path = PIPELINES_DIR / f"{name}.yaml"
    if not path.is_file():
        raise ValueError(f"子 pipeline {name!r} 不存在（{path}）")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取子 pipeline {name!r}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except Exception as exc:
        raise ValueError(f"子 pipeline {name!r} 的 YAML 无效: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"子 pipeline {name!r} 的 YAML 顶层必须是映射")
    return PipelineConfig(**data)
