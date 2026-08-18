"""
Run 编排服务 — approver/事件落库/执行/重启恢复。

从旧 main.py 的 run plumbing 一一对应抽出，Web 层只做路由与参数校验。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.core import database
from app.core.logging import get_logger
from app.demo import PIPELINE_PATH
from app.engine import DAG, NodeResult, NodeStatus, load_dag
from app.engine.node import ApproverFunc, NodeEventFunc
from app.models.run import RunRecord

logger = get_logger(__name__)


def make_approver(record: RunRecord) -> ApproverFunc:
    """Build the approver for one run: 把节点状态置为 reviewing 并落库，然后
    轮询决策表直到 /api/approve 写入决策。"""

    async def approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = record.nodes.setdefault(node_name, {})
        entry["status"] = NodeStatus.REVIEWING.value
        entry["payload"] = payload
        await database.save(record)
        while True:
            await asyncio.sleep(0.3)
            decision = await database.take_decision(record.id, node_name)
            if decision is not None:
                entry["status"] = NodeStatus.RUNNING.value
                await database.save(record)
                return decision

    return approver


def make_event_sink(record: RunRecord) -> NodeEventFunc:
    """Build the on_event sink for one run: 节点状态变化写进快照并落库。
    """

    async def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = result.to_dict()
        try:
            await database.save(record)
        except Exception as exc:
            logger.error("Failed to save run snapshot: %s", exc)

    return on_event


async def run_pipeline(
    record: RunRecord,
    dag: DAG,
    resume: dict[str, dict[str, Any]] | None = None,
) -> None:
    try:
        await dag.run(resume=resume)
        record.status = "completed"
    except asyncio.CancelledError:
        record.status = "cancelled"
        raise
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        record.status = "failed"
    finally:
        record.finished_at = datetime.now().isoformat(timespec="seconds")
        await database.save(record)


async def create_run() -> int:
    """校验配置并落库一个新 run，返回 run_id。
    """
    record = RunRecord(
        name=PIPELINE_PATH.name,
        config_file=PIPELINE_PATH.name,
        mermaid="",
        created_at=datetime.now().isoformat(timespec="seconds"),
        status="running",
    )
    dag = load_dag(
        PIPELINE_PATH,
        approver=make_approver(record),
        on_event=make_event_sink(record),
    )
    await database.save(record)
    record.name = dag.name
    record.mermaid = dag.to_mermaid()
    await database.save(record)
    asyncio.create_task(run_pipeline(record, dag))
    return record.id


async def resume_record(record: RunRecord) -> None:
    """重启后恢复未完成的 run：已完成节点快照重建上下文、重跑剩余部分。

    审批节点会重新挂起继续等决策——决策表里没被消费的决策会被恢复后
    的审批器继续消费，审批不丢。"""
    if not record.config_file:
        record.status = "cancelled"
        await database.save(record)
        return
    try:
        dag = load_dag(
            PIPELINE_PATH.parent / record.config_file,
            approver=make_approver(record),
            on_event=make_event_sink(record),
        )
    except ValueError as exc:
        record.status = "failed"
        record.error = f"resume failed: {exc}"
        await database.save(record)
        return
    asyncio.create_task(run_pipeline(record, dag, resume=record.nodes))


async def resume_stuck_runs() -> None:
    """启动时恢复上次进程退出时仍在 running 的 run（见 lifespan）。"""
    for record in (await database.load()).values():
        if record.status == "running":
            await resume_record(record)
