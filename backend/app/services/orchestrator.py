"""Run 编排服务 — 执行生命周期：审批器/事件落库、执行、挂起-恢复、启动选主。

从 services/runs.py 抽出的执行侧逻辑，RunRecord 与审批决策的落库
分别见 services/runs.py、services/reviews.py。审批节点挂起（节点 REVIEWING
落库、run 状态置为 reviewing 后 run 任务干净退出），/api/approve 写决策后
resume_record 续跑，
与重启恢复共用同一套重放机制；决策消费是原子的（take_decision 单条
DELETE..RETURNING），并发续跑恰好一个消费者。多 worker 启动恢复靠
advisory lock 选主、仅 leader 执行扫描续跑。create_run /
resume_stuck_runs 是对外入口（路由与 lifespan），其余为内部实现。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core import database
from app.core.config import settings
from app.core.logging import get_logger
from app.engine import DAG, NodeResult, NodeStatus, SuspendExecution, load_dag
from app.engine.node import ApproverFunc
from app.engine.types import NodeEventFunc
from app.models.run import RunRecord
from app.services import reviews, runs

logger = get_logger(__name__)


def make_approver(record: RunRecord) -> ApproverFunc:
    """Build the approver for one run: 首次到达把节点状态置为 reviewing、
    run 状态也置为 reviewing（对外暴露"需要人工审核"）落库后挂起退出；
    续跑时先消费决策表——有决策把 run 状态改回 running 直接返回，
    无决策再次挂起"""

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = record.nodes.setdefault(node_name, {})
        decision = await reviews.take_decision(record.id, node_name)
        if decision is not None:
            entry["status"] = NodeStatus.RUNNING.value
            await runs.save(record)
            return decision
        entry["status"] = NodeStatus.REVIEWING.value
        entry["payload"] = payload
        await runs.save(record)
        raise SuspendExecution(f"run {record.id} 节点 {node_name} 等待人工审批")

    return approver


def make_event_sink(record: RunRecord) -> NodeEventFunc:
    """Build the on_event sink for one run: 节点状态变化写进快照并落库。
    """

    async def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = result.to_dict()
        try:
            await runs.save(record)
        except Exception as exc:
            logger.error("Failed to save run snapshot: %s", exc)

    return on_event


async def run_pipeline(
    record: RunRecord,
    dag: DAG,
    resume: dict[str, dict[str, Any]] | None = None,
) -> None:
    """执行一次 run：dag.run 返回 → completed；挂起 → 保持 running 干净退出
    （等 /approve 续跑）；异常 → failed。finished_at 只在终态写入。"""
    try:
        await dag.run(resume=resume)
        record.status = "completed"
    except asyncio.CancelledError:
        record.status = "cancelled"
        record.finished_at = datetime.now().isoformat(timespec="seconds")
        await runs.save(record)
        raise
    except SuspendExecution:
        record.status = "reviewing"
        await runs.save(record)
        return
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        record.status = "failed"
    record.finished_at = datetime.now().isoformat(timespec="seconds")
    await runs.save(record)


async def create_run(config_file: str | None = None) -> int:
    """校验配置并落库一个新 run，返回 run_id。config_file 缺省用人工审核演示。
    """
    config_file = config_file or "05_human_review.yaml"
    path = settings.PIPELINES_DIR / config_file
    if not path.is_file():
        raise ValueError(f"未知的流水线配置 {config_file!r}")
    record = RunRecord(
        name=path.name,
        config_file=path.name,
        mermaid="",
        created_at=datetime.now().isoformat(timespec="seconds"),
        status="running",
    )
    dag = load_dag(
        path,
        approver=make_approver(record),
        on_event=make_event_sink(record),
    )
    record.name = dag.name
    record.mermaid = dag.to_mermaid()
    await runs.save(record)
    asyncio.create_task(run_pipeline(record, dag))
    return record.id


async def resume_record(record: RunRecord) -> None:
    """审批触发或重启后恢复 run：重放 DAG 续跑。"""
    dag = load_dag(
        settings.PIPELINES_DIR / record.config_file,
        approver=make_approver(record),
        on_event=make_event_sink(record),
    )
    record.status = "running"
    await runs.save(record)
    asyncio.create_task(run_pipeline(record, dag, resume=record.nodes))


async def _acquire_recovery_lock() -> AsyncConnection | None:
    """抢启动恢复选主权（session 级 advisory lock）：成功返回持有的连接，
    失败返回 None。"""
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
    """启动时恢复上次进程退出时仍在 running 或等待审核（reviewing）的 run（见 lifespan）
    """
    raw = await _acquire_recovery_lock()
    if raw is None:
        return
    try:
        rows, _ = await runs.list_runs()
        for record in rows:
            if record.status in ("running", "reviewing"):
                await resume_record(record)
    finally:
        await _release_recovery_lock(raw)
