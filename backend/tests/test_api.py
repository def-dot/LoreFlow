"""API 集成测试 — run 生命周期 + 审批流 + 错误信封 + 重启恢复"""

import asyncio
from typing import Any

from httpx import AsyncClient

from app.models.run import RunRecord
from app.services import orchestrator
from app.services import runs as run_service

# ---------------------------------------------------------------------------
# 轮询辅助
# ---------------------------------------------------------------------------


async def _wait_terminal(client: AsyncClient, run_id: int, timeout: float = 15) -> dict[str, Any]:
    """轮询 run 直到终态（completed/failed/cancelled）。"""

    async def poll() -> dict[str, Any]:
        while True:
            resp = await client.get(f"/api/v1/runs/{run_id}")
            assert resp.status_code == 200
            data = resp.json()["data"]
            if data["status"] != "running":
                return data
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def _wait_reviewing(client: AsyncClient, run_id: int, timeout: float = 15) -> dict[str, Any]:
    """轮询 run 直到出现待审批节点。"""

    async def poll() -> dict[str, Any]:
        while True:
            resp = await client.get(f"/api/v1/runs/{run_id}")
            data = resp.json()["data"]
            if data["reviewing"]:
                return data
            if data["status"] != "running":
                raise AssertionError(f"run 在审批前已结束: {data}")
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def _create_and_approve(client: AsyncClient, approve: bool = True) -> int:
    """建一个 run 并走完审批流，返回 run_id。"""
    resp = await client.post("/api/v1/runs")
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    await _wait_reviewing(client, run_id)
    resp = await client.post(
        f"/api/v1/runs/{run_id}/approve/review",
        json={"approve": approve, "reason": None if approve else "Rejected in test"},
    )
    assert resp.status_code == 200
    await _wait_terminal(client, run_id)
    return run_id


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


async def test_node_types_catalog(client: AsyncClient) -> None:
    """注册表目录：枚举全部类型，条件谓词单独成类，不含函数实现。"""
    resp = await client.get("/api/v1/node-types")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["msg"] == "ok"

    types = body["data"]["node_types"]
    names = {t["name"] for t in types}
    expected = {"cfg_fetch", "cfg_clean", "cfg_enrich", "cfg_merge", "cfg_publish", "cfg_report", "cfg_needs_report"}
    assert expected <= names
    assert set(types[0]) == {"name", "kind", "label", "description"}

    conditions = [t["name"] for t in types if t["kind"] == "condition"]
    assert conditions == ["cfg_needs_report"]


async def test_run_lifecycle_approve(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/runs")
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == 201 and body["msg"] == "ok"
    run_id = body["data"]["run_id"]

    data = await _wait_reviewing(client, run_id)
    assert data["status"] == "running"
    assert "review" in data["reviewing"]
    assert data["nodes"]["review"]["status"] == "reviewing"
    assert data["nodes"]["fetch"]["status"] == "completed"

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.json()["data"] == {"status": "ok", "run_id": run_id, "node": "review", "approve": True}

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["publish"]["status"] == "completed"
    assert data["nodes"]["report"]["status"] == "completed"
    assert data["error"] is None


async def test_run_lifecycle_reject(client: AsyncClient) -> None:
    run_id = await _create_and_approve(client, approve=False)
    resp = await client.get(f"/api/v1/runs/{run_id}")
    data = resp.json()["data"]
    assert data["status"] == "failed"
    assert data["nodes"]["review"]["status"] == "failed"
    assert data["nodes"]["review"]["error"] == "Rejected in test"
    assert data["nodes"]["publish"]["status"] == "skipped"
    assert "DAGExecutionError" in data["error"]


async def test_list_runs_sorted_desc(client: AsyncClient) -> None:
    first = await _create_and_approve(client)
    await asyncio.sleep(1.1)  # created_at 时间戳粒度为秒，拉开两位
    second = await _create_and_approve(client)

    resp = await client.get("/api/v1/runs")
    body = resp.json()
    assert body["code"] == 200
    runs = body["data"]
    assert [r["id"] for r in runs] == [second, first]
    fields = set(runs[0])
    assert fields == {"id", "name", "created_at", "finished_at", "status", "error"}
    assert "T" not in runs[0]["created_at"]  # 响应层日期用空格分隔


async def test_unknown_run_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/runs/99999")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404 and body["msg"] == "运行 99999 不存在" and body["data"] is None


async def test_approve_non_reviewing_node_404(client: AsyncClient) -> None:
    run_id = await _create_and_approve(client)
    resp = await client.post(f"/api/v1/runs/{run_id}/approve/publish", json={"approve": True})
    assert resp.status_code == 404
    assert "不在等待审核" in resp.json()["msg"]


async def test_validation_error_422(client: AsyncClient) -> None:
    run_id = await _create_and_approve(client)
    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"bogus": 1})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == 422 and body["msg"] == "参数校验失败"


async def test_invalid_pipeline_400_no_run_record(client: AsyncClient, monkeypatch, tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: bad\nnodes:\n  a:\n    type: no_such_fn\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "PIPELINE_PATH", bad)

    resp = await client.post("/api/v1/runs")
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400
    assert "no_such_fn" in body["msg"]

    # 配置错误不产生垃圾 run 记录
    resp = await client.get("/api/v1/runs")
    assert resp.json()["data"] == []


async def test_resume_stuck_run(client: AsyncClient) -> None:
    """模拟崩溃重启：running 记录 + 部分节点快照 → resume 续跑。"""
    record = RunRecord(
        name="content_pipeline",
        config_file="pipeline.yaml",
        mermaid="graph TD\n",
        created_at="2026-01-01T00:00:00",
        status="running",
        nodes={
            "fetch": {
                "status": "completed",
                "output": {"title": "DAG Flow v0.1", "body": "  declarative config rocks  "},
                "error": None,
                "attempts": 1,
                "duration_ms": 1,
            },
        },
    )
    await run_service.save(record)
    run_id = record.id
    assert run_id is not None

    await orchestrator.resume_stuck_runs()

    data = await _wait_reviewing(client, run_id)
    assert data["nodes"]["fetch"]["status"] == "completed"  # 已完成节点不重跑

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.status_code == 200

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["publish"]["status"] == "completed"
