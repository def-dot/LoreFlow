"""Run 编排服务 — 执行生命周期：审批器/事件落库、执行、挂起-恢复、启动选主。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import yaml

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core import database
from app.core.logging import get_logger

from app.engine import (
    DAG,
    NodeResult,
    NodeStatus,
    SuspendExecution,
    load_dag,
)
from app.engine.node import ApproverFunc
from app.engine.types import NodeEventFunc
from app.engine.validate import validate_inputs
from app.models.run import RunRecord, RunStatus
from app.services import runs
from app.services.pipelines import get_pipeline

logger = get_logger(__name__)


def make_approver(record: RunRecord) -> ApproverFunc:
    """人工审核时挂起；审核后恢复。
    """

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = record.nodes.setdefault(node_name, {})
        output = entry.get("output") or {}
        if output.get("decision"):
            return output["decision"]

        raise SuspendExecution(f"run {record.id} 节点 {node_name} 等待人工审批", {"payload": payload})

    return approver


def make_event_sink(record: RunRecord) -> NodeEventFunc:
    """节点状态变化写进快照并落库（只写 nodes，status 归 CAS 独占）。
    """

    async def on_event(result: NodeResult) -> None:
        entry = result.to_dict()
        prev = record.nodes.get(result.node_name) or {}
        prev_output = prev.get("output")
        if entry.get("output") is None and prev_output is not None:
            entry["output"] = prev_output

        if result.status is NodeStatus.RETRYING:
            entry["attempts_log"] = (prev.get("attempts_log") or []) + [
                {
                    "attempt": result.attempts,
                    "error": entry.get("error"),
                    "at": datetime.now().isoformat(timespec="seconds"),
                }
            ]
        record.nodes[result.node_name] = {**prev, **entry}
        try:
            await runs.save_nodes(record)
        except Exception as exc:
            logger.error("Failed to save run snapshot: %s", exc)

    return on_event


async def approve_and_resume(record: RunRecord, node_name: str, decision: dict[str, Any]) -> None:
    """决策写进节点快照并持久化，随后恢复执行（approve 端点的全部动作）。
    """
    decision = {**decision, "approved_at": datetime.now().isoformat(timespec="seconds")}
    entry = record.nodes.setdefault(node_name, {})
    entry.setdefault("output", {})["decision"] = decision
    await runs.save_nodes(record)
    await resume_record(record)


async def run_pipeline(record: RunRecord, dag: DAG) -> None:
    """执行一次 run。挂起与终态均走 CAS 裁决：与取消/approve 并发时谁先抢到算谁的。
    """
    outcome = RunStatus.COMPLETED
    error: str | None = None
    try:
        await dag.run(inputs=record.inputs, resume=record.nodes)
    except asyncio.CancelledError:
        outcome = RunStatus.CANCELLED
        error = "用户手动取消"
    except SuspendExecution:
        outcome = RunStatus.REVIEWING
    except Exception as exc:
        outcome = RunStatus.FAILED
        error = str(exc)
    finally:
        # 挂起非终态：不写 finished_at（outcome 只会是 COMPLETED/CANCELLED/
        # REVIEWING/FAILED，永不是 RUNNING）
        values: dict[str, Any] = {
            "status": outcome,
            "finished_at": datetime.now().isoformat(timespec="seconds") if outcome is not RunStatus.REVIEWING else None,
            "error": error,
        }

        async with database.AsyncSessionLocal() as session:
            result = await session.execute(
                update(RunRecord)
                .where(RunRecord.id == record.id, RunRecord.status == RunStatus.RUNNING)
                .values(**values)
            )
            await session.commit()
        if not result.rowcount:
            logger.info("[run %s] %s 未落库：状态已被并发修改（如取消）", record.id, values["status"].value)


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
    pipeline: str | None = None,
    name: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> int:
    """校验配置并落库一个新 run，返回 run_id。"""
    if not pipeline:
        raise ValueError("pipeline 必填")
    inputs = dict(inputs) if inputs else {}
    text, config = get_pipeline(pipeline)
    dag = load_dag(config)
    record = RunRecord(
        name=name or dag.name,  # 用户自定义或用工作流名称
        pipeline=dag.name,  # 工作流名称（YAML 的 name 字段）
        mermaid="",
        created_at=datetime.now().isoformat(timespec="seconds"),
        status=RunStatus.RUNNING,
    )
    dag.approver = make_approver(record)
    dag.on_event = make_event_sink(record)
    record.definition = text

    errors = validate_inputs(inputs, dag.params)
    if errors:
        raise ValueError("\n".join(errors))

    record.inputs = {**dag.default_inputs, **inputs}
    record.mermaid = dag.to_mermaid()

    await runs.create(record)
    task = asyncio.create_task(run_pipeline(record, dag))
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
                finished_at=datetime.now().isoformat(timespec="seconds"),
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
   
    dag = load_dag(
        yaml.safe_load(record.definition),
        approver=make_approver(record),
        on_event=make_event_sink(record),
    )
  
    task = asyncio.create_task(run_pipeline(record, dag))
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
