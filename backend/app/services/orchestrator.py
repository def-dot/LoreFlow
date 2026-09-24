"""Run 编排服务 — 执行生命周期：事件落库、执行、挂起-恢复、启动选主。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import yaml

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core import database
from app.core.logging import get_logger

from app.engine import (
    Pipeline,
    NodeResult,
    SuspendExecution,
)
from app.engine.pipeline import validate_inputs
from app.models.pipeline import PipelineRecord
from app.models.run import RunRecord, RunStatus
from app.services import pipelines as pipelines_service
from app.services import runs

logger = get_logger(__name__)


async def _ensure_pipeline_record(name: str, description: str, definition: str) -> PipelineRecord:
    """获取或创建 PipelineRecord（按 name 去重）。"""
    async with database.AsyncSessionLocal() as session:
        result = await session.execute(
            select(PipelineRecord).where(PipelineRecord.name == name)
        )
        record = result.scalar_one_or_none()
        if record is None:
            record = PipelineRecord(name=name, description=description, definition=definition)
            session.add(record)
            await session.commit()
            await session.refresh(record)
        else:
            changed = False
            if record.definition != definition:
                record.definition = definition
                changed = True
            if record.description != description:
                record.description = description
                changed = True
            if changed:
                record.updated_at = datetime.now()
                await session.commit()
                await session.refresh(record)
        return record


async def run_pipeline(record: RunRecord) -> None:
    """执行一次 run。
    """
    pipeline = Pipeline.model_validate(yaml.safe_load(record.definition))

    async def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = result.model_dump(mode="json")
        try:
            async with database.AsyncSessionLocal() as session:
                await session.execute(
                    update(RunRecord).where(RunRecord.id == record.id).values(nodes=record.nodes)
                )
                await session.commit()
        except Exception as exc:
            logger.error("Failed to save run snapshot: %s", exc)

    error: str | None = None
    output: dict[str, Any] | None = None
    try:
        _, output = await pipeline.run(
            inputs=record.inputs,
            on_event=on_event,
            resume=record.nodes,
        )
        outcome = RunStatus.COMPLETED
    except asyncio.CancelledError:
        outcome = RunStatus.CANCELLED
        error = "用户手动取消"
    except SuspendExecution:
        outcome = RunStatus.REVIEWING
    except Exception as exc:
        outcome = RunStatus.FAILED
        error = str(exc)
    finally:
        async with database.AsyncSessionLocal() as session:
            result = await session.execute(
                update(RunRecord)
                .where(RunRecord.id == record.id, RunRecord.status == RunStatus.RUNNING)
                .values(
                    status=outcome,
                    output=output,
                    error=error,
                    finished_at=datetime.now() if outcome is not RunStatus.REVIEWING else None,
                )
            )
            await session.commit()
        if not result.rowcount:
            logger.info("[run %s] %s 未落库：状态已被并发修改（如取消）", record.id, outcome.value)


async def _cancel_watchdog(run_id: int, pipeline: asyncio.Task[None], interval: float = 1.0) -> None:
    """伴随 pipeline 的取消看门狗：周期查 DB，发现 CANCELLED 就中断本进程 task。
    """
    while not pipeline.done():
        await asyncio.sleep(interval)
        try:
            record = await runs.get_run(run_id)
        except Exception:
            continue
        if record is not None and record.status == RunStatus.CANCELLED:
            pipeline.cancel()
            return


async def create_run(
    pipeline_id: int,
    name: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> int:
    """校验配置并落库一个新 run，返回 run_id。"""
    inputs = dict(inputs) if inputs else {}

    pipeline_rec = await pipelines_service.get_pipeline(pipeline_id)
    if pipeline_rec is None:
        raise ValueError(f"流水线 {pipeline_id} 不存在")

    raw = pipeline_rec.definition
    config = yaml.safe_load(raw)
    pipeline = Pipeline.model_validate(config)
    validate_inputs(pipeline.params or {}, inputs)

    record = RunRecord(
        name=name or pipeline.name,
        pipeline_id=pipeline_rec.id,
        pipeline_name=pipeline.name,
        status=RunStatus.RUNNING,
        definition=raw,
        inputs=inputs,
    )
    async with database.AsyncSessionLocal() as session:
        session.add(record)
        await session.commit()
        await session.refresh(record)

    task = asyncio.create_task(run_pipeline(record))
    watchdog = asyncio.create_task(_cancel_watchdog(record.id, task))
    task.add_done_callback(lambda _: watchdog.cancel())
    return record.id


async def cancel_run(run_id: int) -> None:
    """取消运行中或待审核的 run：CAS 抢状态（纯 DB 操作，不触碰进程内 task）。
    """
    record = await runs.get_run(run_id)
    if record is None:
        raise ValueError(f"运行 {run_id} 不存在")
    if record.status in runs.TERMINAL_STATUSES:
        raise ValueError(f"仅可取消运行中或待审核的 run（当前：{record.status.value}）")

    async with database.AsyncSessionLocal() as session:
        result = await session.execute(
            update(RunRecord)
            .where(
                RunRecord.id == run_id,
                RunRecord.status.in_({RunStatus.RUNNING, RunStatus.REVIEWING}),
            )
            .values(
                status=RunStatus.CANCELLED,
                error="用户手动取消",
                finished_at=datetime.now(),
            )
        )
        await session.commit()
    if not result.rowcount:
        fresh = await runs.get_run(run_id)
        raise ValueError(f"取消失败：状态已变为 {fresh.status.value if fresh else '未知'}")


async def resume_record(record: RunRecord) -> None:
    """审批触发或重启后恢复 run：按钉住的定义重放 DAG 续跑。
    """
    async with database.AsyncSessionLocal() as session:
        result = await session.execute(
            update(RunRecord)
            .where(
                RunRecord.id == record.id,
                RunRecord.status.in_({RunStatus.RUNNING, RunStatus.REVIEWING}),
            )
            .values(status=RunStatus.RUNNING)
        )
        await session.commit()
    if not result.rowcount:
        logger.info("[run %s] 恢复运行失败：当前状态已是运行中", record.id)
        return

    record.status = RunStatus.RUNNING

    task = asyncio.create_task(run_pipeline(record))
    watchdog = asyncio.create_task(_cancel_watchdog(record.id, task))
    task.add_done_callback(lambda _: watchdog.cancel())


async def _acquire_recovery_lock() -> AsyncConnection | None:
    """抢启动恢复选主权（session 级 advisory lock）"""
    raw = await database.engine.raw_connection()
    got = await raw.driver_connection.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", "resume_lock")
    if not got:
        raw.close()
        return None
    return raw


async def _release_recovery_lock(raw: AsyncConnection) -> None:
    await raw.driver_connection.execute("SELECT pg_advisory_unlock(hashtext($1))", "resume_lock")
    raw.close()


async def resume_stuck_runs() -> None:
    """启动时恢复上次进程退出时仍在 running 或等待审核（reviewing）的 run
    """
    raw = await _acquire_recovery_lock()
    if raw is None:
        return
    try:
        rows, _ = await runs.list_runs()
        for record in rows:
            if record.status in (RunStatus.RUNNING, RunStatus.REVIEWING):
                await resume_record(record)
    finally:
        await _release_recovery_lock(raw)
