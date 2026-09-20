"""流水线浏览与 CRUD（pipelines/*.yaml）。

列表只做轻量解析（快、容错：单个文件坏了跳过并告警，不让整个
目录 500）；详情走 load_dag 的完整校验与图构建（mermaid/拓扑序）。
浏览不执行 dag.run()，无需 approver（human 节点的 approver 缺失只在
真正运行时报错）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import get_logger
from app.engine import RetryPolicy, load_dag
from app.engine.resolve import parse_retry
from app.engine.schema import PipelineConfig
from app.registry import REGISTRY

logger = get_logger(__name__)

PIPELINES_DIR = settings.PIPELINES_DIR


def _start_params(cfg: PipelineConfig) -> dict[str, Any]:
    """从 __start__ 节点提取输入参数声明。"""
    start = cfg.nodes.get("__start__")
    if not start or not start.inputs:
        return {}
    return {k: v.model_dump() if hasattr(v, "model_dump") else v
            for k, v in start.inputs.items()}


def list_pipelines() -> list[dict[str, Any]]:
    """枚举目录下全部 .yaml；单个文件解析失败跳过并告警。"""
    entries: list[dict[str, Any]] = []
    if not PIPELINES_DIR.is_dir():
        return entries
    for path in sorted(PIPELINES_DIR.glob("*.yaml")):
        try:
            _, cfg = _load_pipeline(path)
        except Exception as exc:
            logger.warning("Skip pipeline %s: %s", path.name, exc)
            continue
        entries.append({
            "name": cfg.name or path.stem,
            "description": cfg.description or "",
            "node_count": len(cfg.nodes),
            "params": _start_params(cfg),
        })
    return entries


def _retry_summary(rp: RetryPolicy | None) -> str | None:
    """RetryPolicy → 中文摘要；默认值不展示，max_retries=0 即不重试。"""
    if rp is None:
        return None
    if rp.max_retries == 0:
        return "不重试"
    parts = [f"重试 {rp.max_retries} 次"]
    if rp.backoff_base != 1.0 or rp.backoff_factor != 2.0 or rp.backoff_max != 60.0:
        parts.append(f"退避 {rp.backoff_base:g}s×{rp.backoff_factor:g}（≤{rp.backoff_max:g}s）")
    if rp.retry_on and rp.retry_on != (Exception,):
        parts.append("仅 " + "、".join(c.__name__ for c in rp.retry_on))
    if not rp.jitter:
        parts.append("无抖动")
    return "，".join(parts)


def _load_pipeline(path: Path) -> tuple[str, PipelineConfig]:
    """读取并解析 YAML → (原文, PipelineConfig)。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取配置文件 {path!r}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"配置文件 {path!r} 的 YAML 无效: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("顶层必须是映射(dict)")
    cfg = PipelineConfig(**data)
    return raw, cfg


def get_pipeline(name: str) -> tuple[str, dict[str, Any]]:
    """根据 pipeline name 获取 YAML 原文和解析后的 config dict。不存在 404。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
    raw, cfg = _load_pipeline(path)
    return raw, cfg.model_dump()


def detail_from_config(
    raw: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """已解析的 YAML 配置 → 详情展示数据（图、节点行、YAML 原文）。"""
    cfg = PipelineConfig(**config)
    dag = load_dag(cfg)
    rows: list[dict[str, Any]] = []
    for name in dag.topological_order():
        spec = cfg.nodes.get(name)
        type_val = spec.type if spec else None

        # pipeline 节点 / 虚拟节点的 FuncDef 在 dag 对象中，不在全局 REGISTRY
        node = dag.nodes.get(name)
        if node and node.func_def:
            node_type = node.func_def
        else:
            node_type = REGISTRY.get(type_val) if type_val else None

        # depends_on 优先从 node 取（虚拟节点无 spec，但 node 有 depends_on）
        deps = list(node.depends_on) if node else (list(spec.depends_on) if spec else [])

        row: dict[str, Any] = {
            "name": name,
            "label": spec.label if spec else (node.label if node else None),
            "type": type_val,
            "type_label": node_type.label if node_type else None,
            "description": spec.description if spec else None,
            "type_description": node_type.description if node_type else None,
            "type_input_schema": node_type.input_schema.model_json_schema() if node_type and node_type.input_schema else None,
            "type_output_schema": node_type.output_schema.model_json_schema() if node_type and node_type.output_schema else None,
            "depends_on": deps,
            "inputs": spec.inputs if spec else None,
            "retry": _retry_summary(parse_retry(spec.retry)) if spec else None,
            "condition": spec.condition if spec else None,
        }
        rows.append(row)

    return {
        "name": dag.name,
        "description": cfg.description or "",
        "node_count": len(dag.node_names),
        "mermaid": dag.to_mermaid(),
        "source": raw,
        "nodes": rows,
        "params": _start_params(cfg),
    }


# ---------------------------------------------------------------------------
# Pipeline CRUD
# ---------------------------------------------------------------------------


def create_pipeline(definition: str) -> str:
    """创建 pipeline 文件：校验 YAML → 写入目录。返回 name。"""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = PipelineConfig.model_validate(data)
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    dest = PIPELINES_DIR / f"{cfg.name}.yaml"
    if dest.is_file():
        raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
    dest.write_text(definition, encoding="utf-8")
    return cfg.name


def update_pipeline(name: str, definition: str) -> str:
    """更新 pipeline 文件。如果 YAML name 变了，自动重命名文件。返回最终 name。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = PipelineConfig.model_validate(data)
    new_path = PIPELINES_DIR / f"{cfg.name}.yaml"
    if new_path != path:
        if new_path.is_file():
            raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
        new_path.write_text(definition, encoding="utf-8")
        path.unlink()
        return cfg.name
    path.write_text(definition, encoding="utf-8")
    return name


def delete_pipeline(name: str) -> bool:
    """删除 pipeline 文件。不存在返回 False。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        return False
    path.unlink()
    return True
