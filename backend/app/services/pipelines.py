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
from sqlalchemy import text
from sqlmodel import select

from app.core import database
from app.core.config import settings
from app.core.logging import get_logger

from app.engine.pipeline import Pipeline

logger = get_logger(__name__)
from app.models.pipeline import PipelineRecord
from app.registry import REGISTRY
from app.registry.pipeline_tool import register_pipeline_tool, unregister_pipeline_tool

PIPELINES_DIR = settings.PIPELINES_DIR


async def sync_pipelines_from_yaml() -> None:
    """启动时将 pipelines/*.yaml 同步到数据库（存在则更新，不存在则插入）。

    使用 pg_try_advisory_lock 保证多 worker 下只有一个进程执行。
    整体异常兜底——不抛出，避免阻塞启动。
    """
    try:
        if not PIPELINES_DIR.is_dir():
            return

        yaml_files = sorted(PIPELINES_DIR.glob("*.yaml"))
        if not yaml_files:
            return

        async with database.AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext('pipelines_yaml_sync'))")
            )
            locked = result.scalar()
            if not locked:
                return

            logger.info("同步 pipelines YAML → 数据库 (%d 个文件)", len(yaml_files))

            for f in yaml_files:
                try:
                    raw = f.read_text(encoding="utf-8")
                    data = yaml.safe_load(raw)
                    cfg = Pipeline.model_validate(data)

                    stmt = select(PipelineRecord).where(PipelineRecord.name == cfg.name)
                    result = await session.execute(stmt)
                    existing = result.scalars().first()

                    if not existing:
                        session.add(PipelineRecord(
                            name=cfg.name,
                            description=cfg.description or "",
                            agent_tool=cfg.metadata.agent_tool,
                            definition=raw,
                        ))

                    # 注册为 Agent 工具
                    register_pipeline_tool(cfg)
                except Exception:
                    logger.warning("跳过 %s", f.name, exc_info=True)

            await session.commit()

        logger.info("Pipelines 同步完成")
    except Exception:
        logger.exception("Pipelines YAML 同步失败")


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
        row["type_input_schema"] = node_type.json_input_schema() if node_type else None
        row["type_output_schema"] = node_type.json_output_schema() if node_type else None
        rows.append(row)

    # params 需要转为 plain dict（ParamSchema 不可直接 JSON 序列化）
    params_dict = (
        {k: v.model_dump() for k, v in pipeline.params.items()}
        if pipeline.params else None
    )

    return {
        "name": pipeline.name or "",
        "description": pipeline.description or "",
        "params": params_dict,
        "required": pipeline.required,
        "node_count": len(pipeline.nodes),
        "mermaid": pipeline.to_mermaid(),
        "source": raw,
        "nodes": rows,
    }


# ---------------------------------------------------------------------------
# Pipeline CRUD（DB）
# ---------------------------------------------------------------------------


async def create_pipeline(definition: str) -> PipelineRecord:
    """创建 pipeline：校验 YAML → 写入 DB → 注册工具。"""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)

    async with database.AsyncSessionLocal() as session:
        exists = await session.exec(select(PipelineRecord).where(PipelineRecord.name == cfg.name))
        if exists.first():
            raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
        rec = PipelineRecord(name=cfg.name, description=cfg.description or "", agent_tool=cfg.metadata.agent_tool, definition=definition)
        session.add(rec)
        await session.commit()
        await session.refresh(rec)

    # 注册为 Agent 工具
    register_pipeline_tool(cfg)
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
    """更新 pipeline：校验 YAML → 更新 DB → 重新注册工具。name 变更时检查冲突。"""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)

    async with database.AsyncSessionLocal() as session:
        rec = await session.get(PipelineRecord, pipeline_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"流水线 {pipeline_id} 不存在")
        old_name = rec.name
        # name 变了，检查新 name 是否冲突
        if cfg.name != rec.name:
            dup = await session.exec(select(PipelineRecord).where(PipelineRecord.name == cfg.name))
            if dup.first():
                raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
        rec.name = cfg.name
        rec.description = cfg.description or ""
        rec.agent_tool = cfg.metadata.agent_tool
        rec.definition = definition
        rec.updated_at = datetime.now()
        await session.commit()
        await session.refresh(rec)

    # 更新工具注册
    unregister_pipeline_tool(old_name)
    register_pipeline_tool(cfg)
    return rec


async def delete_pipeline(pipeline_id: int) -> bool:
    """删除 pipeline。不存在返回 False。"""
    async with database.AsyncSessionLocal() as session:
        rec = await session.get(PipelineRecord, pipeline_id)
        if rec is None:
            return False
        pipeline_name = rec.name
        await session.delete(rec)
        await session.commit()

    # 移除工具注册
    unregister_pipeline_tool(pipeline_name)
    return True
