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
        # 挂起写入走 CAS（仅 RUNNING 可转 REVIEWING）：与取消毫秒级并发时
        # 谁先抢到算谁的。抢不到 = 已被取消 —— 照常挂起退出（task 随即
        # 结束，看门狗已停），DB 留在 CANCELLED，后续 approve 会被
        # resume 的 CAS 拦住
        async with database.AsyncSessionLocal() as session:
            result = await session.execute(
                update(RunRecord)
                .where(RunRecord.id == record.id, RunRecord.status == RunStatus.RUNNING)
                .values(status=RunStatus.REVIEWING)
            )
            await session.commit()
        if not result.rowcount:
            logger.info("[run %s] 挂起放弃：状态已被并发转移（可能已取消）", record.id)
            raise SuspendExecution(f"run {record.id} 节点 {node_name} 等待人工审批（已被取消）")
        await runs.save_nodes(record.id, record.nodes)
        raise SuspendExecution(f"run {record.id} 节点 {node_name} 等待人工审批")

    return approver


def make_event_sink(record: RunRecord) -> NodeEventFunc:
    """节点状态变化写进快照并落库（只写 nodes，status 归 CAS 独占）。"""

    async def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = result.to_dict()
        try:
            await runs.save_nodes(record.id, record.nodes)
        except Exception as exc:
            logger.error("Failed to save run snapshot: %s", exc)

    return on_event


async def run_pipeline(record: RunRecord, dag: DAG) -> None:
    """执行一次 run。终态走 CAS 裁决：与取消/approve 并发时谁先抢到算谁的。

    取消统一由 _cancel_watchdog 感知（取消方只写 DB）：看门狗发现 DB 已
    取消就 task.cancel()，本协程落 CancelledError 分支；resume_stuck_runs
    不复活 CANCELLED（进程重启后）。节点快照由 on_event/approver 逐节点
    落库，这里只裁决 run 级状态。
    """
    outcome = RunStatus.COMPLETED
    error: str | None = None
    suspended = False
    try:
        await dag.run(inputs=record.inputs, resume=record.nodes)
    except asyncio.CancelledError:
        # 吞掉而非 re-raise：终态写入在 finally，re-raise 后其中的 await
        # 有被同一取消再打断的风险；此 task 无下游消费者，正常返回即可
        outcome = RunStatus.CANCELLED
        error = "用户手动取消"
    except SuspendExecution:
        # 挂起：节点+run 两层状态已由 approver 落库，不走终态 CAS
        suspended = True
    except Exception as exc:
        outcome = RunStatus.FAILED
        error = str(exc)
    finally:
        if not suspended:
            # 终态 CAS（仅 RUNNING 可转终态）：抢不到说明 cancel 已先行落了
            # CANCELLED —— 正是要的结果。shield：dag.run 内部的取消请求
            # 可能仍挂在当前 task 上，终态写入必须完成
            values: dict[str, Any] = {
                "status": outcome,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
            if error is not None:
                values["error"] = error  # 仅显式提供时写：None 不覆盖已有值

            async def _finalize() -> None:
                async with database.AsyncSessionLocal() as session:
                    await session.execute(
                        update(RunRecord)
                        .where(RunRecord.id == record.id, RunRecord.status == RunStatus.RUNNING)
                        .values(**values)
                    )
                    await session.commit()

            await asyncio.shield(_finalize())


async def _cancel_watchdog(run_id: int, pipeline: asyncio.Task[None], interval: float = 1.0) -> None:
    """伴随 pipeline 的取消看门狗：周期查 DB，发现 CANCELLED 就中断本进程 task。

    多 worker 下取消发生在别的进程（task.cancel 够不着），本进程靠它感知；
    单 worker 下它是 cancel_run 直接 cancel 打空时的第二击。查询瞬断只跳过
    本周期——看门狗不能先于被看守者死亡。
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


def _spawn(record: RunRecord, dag: DAG) -> None:
    """启动 pipeline + 取消看门狗：中断是执行方自治的事，取消方只写 DB。"""
    task = asyncio.create_task(run_pipeline(record, dag))
    watchdog = asyncio.create_task(_cancel_watchdog(record.id, task))
    task.add_done_callback(lambda _: watchdog.cancel())  # pipeline 已终态（含挂起），看门狗停转


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
    _spawn(record, dag)
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

    恢复本身走 CAS（RUNNING/REVIEWING → RUNNING，覆盖崩溃恢复与审批
    续跑两条入口）：approve 与取消并发时谁先抢到算谁的，已取消（终态）
    的 run 不会被复活。存量旧行无 definition（钉住功能上线前）回退读
    当前文件——历史行为。
    """
    # CAS（RUNNING/REVIEWING → RUNNING，覆盖崩溃恢复与审批续跑两条入口）：
    # approve 与取消并发时谁先抢到算谁的，已取消（终态）的 run 不会被复活
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
        logger.info("[run %s] 恢复放弃：状态已是终态（可能已取消）", record.id)
        return
    record.status = RunStatus.RUNNING  # 内存对齐 DB（挂起 CAS / 日志读内存）

    if record.definition is not None:
        dag = load_dag(
            yaml.safe_load(record.definition),
            approver=make_approver(record),
            on_event=make_event_sink(record),
        )
    else:
        dag = load_dag(
            settings.PIPELINES_DIR / record.config_file,
            approver=make_approver(record),
            on_event=make_event_sink(record),
        )
    _spawn(record, dag)


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
