"""Run 编排服务 — 执行生命周期：审批器/事件落库、执行、挂起-恢复、启动选主。

从 services/runs.py 抽出的执行侧逻辑，RunRecord 与审批决策的落库
分别见 services/runs.py、services/reviews.py。审批节点挂起（节点 REVIEWING
与 run reviewing 同一次落库后 run 任务干净退出），/api/approve 写决策后
resume_record 续跑，
与重启恢复共用同一套重放机制；决策认领是原子的（claim_decision 单条
UPDATE..RETURNING），并发续跑恰好一个消费者，行保留作审计痕迹。认领后
决策同时写进节点快照——进程在"认领到节点完成"窗口内崩溃时，重启重放
从快照原样复用决策，不会把已批准过的节点重新挂起。多 worker 启动恢复
靠 advisory lock 选主、仅 leader 执行扫描续跑。create_run /
resume_stuck_runs 是对外入口（路由与 lifespan），其余为内部实现。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

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
from app.engine.node import ApproverFunc
from app.engine.types import NodeEventFunc
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


def _failure_summary(exc: Exception) -> str:
    """run 失败摘要：DAGExecutionError 只列出失败节点名，这里把各节点
    的失败原因（异常类型 + 消息）逐行附上，供 Runs 页 run-error 展示。"""
    if not isinstance(exc, DAGExecutionError):
        return str(exc)
    lines = [str(exc)]
    for name, result in exc.results.items():
        if result.status == NodeStatus.FAILED and result.error is not None:
            lines.append(f"  {name}: {type(result.error).__name__}: {result.error}")
    return "\n".join(lines)


async def run_pipeline(
    record: RunRecord,
    dag: DAG,
    resume: dict[str, dict[str, Any]] | None = None,
) -> None:
    """执行一次 run：dag.run 返回 → completed；挂起 → 两层 reviewing 已由
    approver 同一次 save 落库，干净退出（等 /approve 续跑）；异常 → failed。
    finished_at 只在终态写入。

    输入回放：YAML 顶层 ``inputs`` 打底，创建 run 时的运行时输入
    （record.inputs，同名键优先）覆盖后传入 ``dag.run`` —— 创建与
    审批/重启 resume 都走这里，运行时输入因此不丢。
    """
    inputs = {**(dag.default_inputs or {}), **(record.inputs or {})}
    try:
        await dag.run(inputs=inputs, resume=resume)
        record.status = RunStatus.COMPLETED
    except asyncio.CancelledError:
        record.status = RunStatus.CANCELLED
        raise
    except SuspendExecution:
        # 挂起：节点+run 两层状态已由 approver 落库，这里不重复写
        return
    except Exception as exc:
        record.error = _failure_summary(exc)
        record.status = RunStatus.FAILED
    finally:
        if record.status != RunStatus.REVIEWING:  # 挂起非终态：finished_at 不写
            record.finished_at = datetime.now().isoformat(timespec="seconds")
        await runs.save(record)


async def create_run(
    config_file: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> int:
    """校验配置并落库一个新 run，返回 run_id。config_file 缺省用人工审核演示。
    inputs 为运行时输入，快照进 record 供 resume 回放（见 run_pipeline）。
    """
    config_file = config_file or "05_human_review.yaml"
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
    dag = load_dag(
        path,
        approver=make_approver(record),
        on_event=make_event_sink(record),
    )
    # 必填输入在落库前校验：缺参的 run 不该创建出来（引擎 run() 还会再兜底一次）
    missing = [k for k in dag.required_inputs if k not in inputs]
    if missing:
        raise ValueError(f"缺少必填输入参数: {', '.join(missing)}")
    # 输入键与节点名共享同一个 ctx 命名空间，重名会被节点输出覆盖 —— 直接拒绝
    clash = sorted(set(inputs) & set(dag.nodes))
    if clash:
        raise ValueError(f"输入参数键与节点名冲突: {', '.join(clash)}")
    record.name = dag.name
    record.mermaid = dag.to_mermaid()
    record.inputs = inputs
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
    record.status = RunStatus.RUNNING
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
            if record.status in (RunStatus.RUNNING, RunStatus.REVIEWING):
                await resume_record(record)
    finally:
        await _release_recovery_lock(raw)
