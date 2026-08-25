"""取消运行 — CAS 状态裁决、看门狗执行侧感知、approve 不复活已取消 run。

多 worker 下取消的正确性 = 三层防线：
- CAS UPDATE：取消/完成/恢复三方竞态谁先抢到算谁的（DB 是唯一裁判）
- save_nodes 定向回写：事件落库不携带旧 status，取消不会被回写复活
- _cancel_watchdog：执行进程轮询 DB 发现取消后中断本进程 task
"""

import asyncio
from datetime import datetime
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update as sa_update

import app.core.database as db_mod
from app.models.run import RunRecord, RunStatus
from app.services import orchestrator, runs


async def _force_cancel(record: RunRecord) -> None:
    """测试脚手架：绕过业务路径直接把 DB 状态置为 CANCELLED（模拟
    取消方已抢到 CAS、执行进程内存尚未感知的时刻）。"""
    async with db_mod.AsyncSessionLocal() as session:
        await session.execute(
            sa_update(RunRecord)
            .where(RunRecord.id == record.id)
            .values(
                status=RunStatus.CANCELLED,
                error="用户手动取消",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        await session.commit()


async def _wait_status(client: AsyncClient, run_id: int, status: str, timeout: float = 10) -> dict[str, Any]:
    """轮询 run 直到指定状态。"""

    async def poll() -> dict[str, Any]:
        while True:
            resp = await client.get(f"/api/v1/runs/{run_id}")
            data = resp.json()["data"]
            if data["status"] == status:
                return data
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def test_save_nodes_leaves_status_alone() -> None:
    """事件回写只动 nodes：取消后的 CANCELLED 不被执行进程的旧内存覆盖（回归）。"""
    record = RunRecord(name="t", config_file="p.yaml", status=RunStatus.RUNNING)
    await runs.save(record)
    await _force_cancel(record)

    # 执行进程的内存 record 仍停在 RUNNING，此刻一个节点完成触发事件回写
    record.nodes["work"] = {"status": "completed", "output": 1}
    await runs.save_nodes(record.id, record.nodes)

    fresh = await runs.get_run(record.id)
    assert fresh is not None
    assert fresh.status == RunStatus.CANCELLED  # 取消存活
    assert fresh.nodes["work"]["output"] == 1  # 快照已落


async def test_watchdog_cancels_pipeline_task() -> None:
    """看门狗发现 DB 已取消 → 中断本进程 task（跨进程取消的执行侧感知）。"""
    record = RunRecord(name="t", config_file="p.yaml", status=RunStatus.RUNNING)
    await runs.save(record)
    await _force_cancel(record)

    victim = asyncio.create_task(asyncio.sleep(30))  # 假装长跑的 pipeline
    watchdog = asyncio.create_task(orchestrator._cancel_watchdog(record.id, victim, interval=0.05))
    try:
        await asyncio.wait_for(victim, timeout=2)
        raise AssertionError("victim 应被看门狗取消")
    except asyncio.CancelledError:
        pass  # 被中断：符合预期
    finally:
        watchdog.cancel()


async def test_cancel_reviewing_run_blocks_approve(client: AsyncClient) -> None:
    """取消待审核 run → 终态 CANCELLED；之后 approve 不会把它复活重跑。"""
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "05_human_review.yaml", "inputs": {"title": "t", "content": "c"}},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    await _wait_status(client, run_id, "reviewing")
    resp = await client.post(f"/api/v1/runs/{run_id}/cancel")
    assert resp.status_code == 200
    assert (await runs.get_run(run_id)).status == RunStatus.CANCELLED

    # 取消不改节点快照（entry 仍是 reviewing），approve 能进路由 —— 但
    # resume 的 CAS（RUNNING/REVIEWING → RUNNING）必须拦住复活
    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.status_code == 200

    await asyncio.sleep(0.3)  # 给可能被错误启动的恢复 task 一点作恶时间
    data = await _wait_status(client, run_id, "cancelled")
    assert data["error"] == "用户手动取消"
    # 已终态再取消：拒绝（严格语义，非幂等成功），状态原样
    resp = await client.post(f"/api/v1/runs/{run_id}/cancel")
    assert resp.status_code == 400
    assert (await runs.get_run(run_id)).status == RunStatus.CANCELLED


async def test_cancel_terminal_run_rejected(client: AsyncClient) -> None:
    """终态 run 不可取消：400 + 状态原样。"""
    record = RunRecord(name="t", config_file="p.yaml", status=RunStatus.COMPLETED)
    await runs.save(record)
    assert record.id is not None

    resp = await client.post(f"/api/v1/runs/{record.id}/cancel")
    assert resp.status_code == 400
    assert "仅可取消" in resp.json()["msg"]
    assert (await runs.get_run(record.id)).status == RunStatus.COMPLETED
