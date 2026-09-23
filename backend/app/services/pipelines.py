"""流水线浏览与 CRUD（pipelines/*.yaml）。

列表只做轻量解析（快、容错：单个文件坏了跳过并告警，不让整个
目录 500）；详情直接遍历 Pipeline + REGISTRY，不构建 DAG。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import get_logger

from app.engine.pipeline import Pipeline
from app.registry import REGISTRY

logger = get_logger(__name__)

PIPELINES_DIR = settings.PIPELINES_DIR


def list_pipelines() -> list[Pipeline]:
    """枚举目录下全部 .yaml；单个文件解析失败跳过并告警。"""
    entries: list[Pipeline] = []
    for path in sorted(PIPELINES_DIR.glob("*.yaml")):
        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            cfg = Pipeline.model_construct(**data)
        except Exception as exc:
            logger.warning("Skip pipeline %s: %s", path.name, exc)
            continue
        entries.append(cfg)
    return entries


def detail_from_config(raw: str) -> dict[str, Any]:
    """YAML 原文 → 详情展示数据（图、节点行、YAML 原文）。"""
    pipeline = Pipeline.model_validate(yaml.safe_load(raw))
    rows: list[dict[str, Any]] = []
    for node_cfg in pipeline.nodes:
        row = node_cfg.model_dump()
        node_type = REGISTRY.get(node_cfg.type)
        row["type_label"] = node_type.label if node_type else None
        row["type_description"] = node_type.description if node_type else None
        row["type_input_schema"] = node_type.input_schema.model_json_schema() if node_type and node_type.input_schema else None
        row["type_output_schema"] = node_type.output_schema.model_json_schema() if node_type and node_type.output_schema else None
        rows.append(row)

    return {
        "name": pipeline.name or "",
        "description": pipeline.description or "",
        "params": pipeline.params,
        "node_count": len(pipeline.nodes),
        "mermaid": pipeline.to_mermaid(),
        "source": raw,
        "nodes": rows,
    }


# ---------------------------------------------------------------------------
# Pipeline CRUD
# ---------------------------------------------------------------------------


def create_pipeline(definition: str) -> Pipeline:
    """创建 pipeline 文件：校验 YAML → 写入目录。"""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    dest = PIPELINES_DIR / f"{cfg.name}.yaml"
    if dest.is_file():
        raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
    dest.write_text(definition, encoding="utf-8")
    return cfg


def update_pipeline(name: str, definition: str) -> Pipeline:
    """更新 pipeline 文件。如果 YAML name 变了，自动重命名文件。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)
    new_path = PIPELINES_DIR / f"{cfg.name}.yaml"
    if new_path != path:
        if new_path.is_file():
            raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
        new_path.write_text(definition, encoding="utf-8")
        path.unlink()
        return cfg
    path.write_text(definition, encoding="utf-8")
    return cfg


def delete_pipeline(name: str) -> bool:
    """删除 pipeline 文件。不存在返回 False。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        return False
    path.unlink()
    return True
