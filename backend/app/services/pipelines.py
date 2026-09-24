"""流水线浏览与 CRUD（pipelines/*.yaml）。

列表只做轻量解析（快、容错：单个文件坏了跳过并告警，不让整个
目录 500）；详情直接遍历 Pipeline + REGISTRY，不构建 DAG。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException
from sqlmodel import select

from app.core import database
from app.core.config import settings

from app.engine.pipeline import Pipeline
from app.models.pipeline import PipelineRecord
from app.registry import REGISTRY

PIPELINES_DIR = settings.PIPELINES_DIR


async def list_pipelines(q: str | None = None) -> list[PipelineRecord]:
    """从数据库枚举 PipelineRecord，支持按名称/描述模糊搜索。"""
    stmt = select(PipelineRecord).order_by(PipelineRecord.id)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            (PipelineRecord.name.ilike(pattern)) | (PipelineRecord.description.ilike(pattern))
        )
    async with database.AsyncSessionLocal() as session:
        return list((await session.exec(stmt)).all())


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
# Pipeline CRUD（DB）
# ---------------------------------------------------------------------------


async def create_pipeline(definition: str) -> PipelineRecord:
    """创建 pipeline：校验 YAML → 写入 DB。"""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)

    async with database.AsyncSessionLocal() as session:
        exists = await session.exec(select(PipelineRecord).where(PipelineRecord.name == cfg.name))
        if exists.first():
            raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
        rec = PipelineRecord(name=cfg.name, description=cfg.description or "", definition=definition)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)
        return rec


async def get_pipeline(pipeline_id: int) -> PipelineRecord | None:
    """按 ID 查 pipeline。"""
    async with database.AsyncSessionLocal() as session:
        return await session.get(PipelineRecord, pipeline_id)


async def get_pipeline_by_name(name: str) -> PipelineRecord | None:
    """按名称查 pipeline（供 orchestrator 使用）。"""
    async with database.AsyncSessionLocal() as session:
        result = await session.exec(select(PipelineRecord).where(PipelineRecord.name == name))
        return result.first()


async def update_pipeline(pipeline_id: int, definition: str) -> PipelineRecord:
    """更新 pipeline：校验 YAML → 更新 DB。name 变更时检查冲突。"""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)

    async with database.AsyncSessionLocal() as session:
        rec = await session.get(PipelineRecord, pipeline_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"流水线 {pipeline_id} 不存在")
        # name 变了，检查新 name 是否冲突
        if cfg.name != rec.name:
            dup = await session.exec(select(PipelineRecord).where(PipelineRecord.name == cfg.name))
            if dup.first():
                raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
        rec.name = cfg.name
        rec.description = cfg.description or ""
        rec.definition = definition
        rec.updated_at = datetime.now()
        await session.commit()
        await session.refresh(rec)
        return rec


async def delete_pipeline(pipeline_id: int) -> bool:
    """删除 pipeline。不存在返回 False。"""
    async with database.AsyncSessionLocal() as session:
        rec = await session.get(PipelineRecord, pipeline_id)
        if rec is None:
            return False
        await session.delete(rec)
        await session.commit()
        return True
