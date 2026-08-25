"""Run 编排服务 — 执行生命周期：审批器/事件落库、执行、挂起-恢复、启动选主。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import yaml

from sqlalchemy.ext.asyncio import AsyncConnection

from app.core import database
from app.core.config import settings
from app.core.logging import get_logger
from app.engine import (
    DAG,
    DAGExecutionError,
    NodeResult,
    NodeStatus,
    SuspendExecution,
    load_dag,
)
from app.engine.declarative import read_yaml
from app.engine.node import ApproverFunc
from app.engine.types import NodeEventFunc
from app.engine.validate import validate_inputs
from app.models.run import RunRecord, RunStatus
from app.services import reviews, runs

logger = get_logger(__name__)


def make_approver(record: RunRecord) -> ApproverFunc:
    """人工审核时挂起;审核后恢复"""

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = record.nodes.setdefault(node_name, {})
        if entry.get('output') and entry["output"].get("decision"):
            # 重跑时已审核过
            return entry["output"]["decision"]

        decision = await reviews.claim_decision(record.id, node_name)
        if decision is not None:
            return decision

        entry["status"] = NodeStatus.REVIEWING.value
        entry["payload"] = payload
        record.status = RunStatus.REVIEWING
        await runs.save(record)
        raise SuspendExecution(f"run {record.id} 节点 {node_name} 等待人工审批")

    return approver


def make_event_sink(record: RunRecord) -> NodeEventFunc:
    """节点状态变化写进快照并落库。"""

    async def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = result.to_dict()
        try:
            await runs.save(record)
        except Exception as exc:
            logger.error("Failed to save run snapshot: %s", exc)

    return on_event


async def run_pipeline(record: RunRecord, dag: DAG) -> None:
    """执行一次 run：dag.run 返回 → completed；
    """
    try:
        await dag.run(inputs=record.inputs, resume=record.nodes)
        record.status = RunStatus.COMPLETED
    except asyncio.CancelledError:
        record.status = RunStatus.CANCELLED
        raise
    except SuspendExecution:
        # 挂起：节点+run 两层状态已由 approver 落库，这里不重复写
        return
    except Exception as exc:
        record.error = str(exc)
        record.status = RunStatus.FAILED
    finally:
        if record.status != RunStatus.REVIEWING:
            record.finished_at = datetime.now().isoformat(timespec="seconds")
        await runs.save(record)


async def create_run(
    config_file: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> int:
    """校验配置并落库一个新 run，返回 run_id。
    """
    if not config_file:
        raise ValueError("config_file 必填")
    path = settings.PIPELINES_DIR / config_file
    if not path.is_file():
        raise ValueError(f"未知的流水线配置 {config_file!r}")
    inputs = dict(inputs) if inputs else {}
    record = RunRecord(
        name=path.name,
        config_file=path.name,
        mermaid="",
        created_at=datetime.now().isoformat(timespec="seconds"),
        status=RunStatus.RUNNING,
    )
    # 读一次文件，同时用于构建 DAG 和钉住定义快照（避免两次读取间的竞态）
    text, config = read_yaml(path)
    dag = load_dag(
        config,
        approver=make_approver(record),
        on_event=make_event_sink(record),
    )
    record.definition = text
    
    errors = validate_inputs(inputs, dag.params)
    if errors:
        raise ValueError("\n".join(errors))
    
    record.inputs = {**dag.default_inputs, **inputs}
    record.name = dag.name
    record.mermaid = dag.to_mermaid()
    
    await runs.save(record)
    asyncio.create_task(run_pipeline(record, dag))
    return record.id


def _dag_for_resume(record: RunRecord) -> DAG:
    """恢复用 DAG：优先创建时钉住的 definition（挂起期间文件被改不漂移）。

    存量旧行无快照（NULL）时回退读当前文件（历史行为）。文件缺失或
    内容与快照不一致只告警不阻断——在途 run 永远按它创建时的版本续跑。
    """
    approver, on_event = make_approver(record), make_event_sink(record)
    if record.definition is None:
        return load_dag(settings.PIPELINES_DIR / record.config_file, approver=approver, on_event=on_event)

    path = settings.PIPELINES_DIR / record.config_file
    if path.is_file():
        current = path.read_text(encoding="utf-8")
        if current != record.definition:
            logger.warning(
                "[run %s] %s 自创建后已变更，按创建时钉住的定义续跑", record.id, record.config_file
            )
    # 快照在创建时已经过 read_yaml/load_dag 校验，这里 safe_load 不会失败
    return load_dag(yaml.safe_load(record.definition), approver=approver, on_event=on_event)


async def resume_record(record: RunRecord) -> None:
    """审批触发或重启后恢复 run：按钉住的定义重放 DAG 续跑。"""
    dag = _dag_for_resume(record)
    record.status = RunStatus.RUNNING
    await runs.save(record)
    asyncio.create_task(run_pipeline(record, dag))


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
