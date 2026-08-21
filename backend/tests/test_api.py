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
            if data["status"] in ("completed", "failed", "cancelled"):
                return data
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def _wait_reviewing(client: AsyncClient, run_id: int, timeout: float = 15) -> dict[str, Any]:
    """轮询 run 直到出现待审批节点。"""

    async def poll() -> dict[str, Any]:
        while True:
            resp = await client.get(f"/api/v1/runs/{run_id}")
            data = resp.json()["data"]
            if any(n.get("status") == "reviewing" for n in data["nodes"].values()):
                return data
            if data["status"] not in ("running", "reviewing"):
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
    """注册表目录：内置 + 插件类型全部枚举，条件谓词单独成类，不含函数实现。"""
    resp = await client.get("/api/v1/node-types")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["msg"] == "ok"

    types = body["data"]["node_types"]
    names = {t["name"] for t in types}
    expected = {
        "cfg_fetch",
        "cfg_clean",
        "cfg_enrich",
        "cfg_merge",
        "cfg_publish",
        "cfg_report",
        "cfg_needs_report",
        "demo_flaky",
        "demo_tick",
        "demo_keep_iterating",
        "demo_needs_review",
    }
    assert expected <= names
    assert set(types[0]) == {"name", "kind", "label", "description"}

    conditions = [t["name"] for t in types if t["kind"] == "condition"]
    assert conditions == ["cfg_needs_report", "demo_keep_iterating", "demo_needs_review", "notify_long_body"]

    notify = {t["name"]: t for t in types}["notify_message"]  # 插件类型随目录自动出现
    assert notify["kind"] == "function" and notify["label"] == "生成通知"


async def test_run_lifecycle_approve(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/runs")
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == 201 and body["msg"] == "ok"
    run_id = body["data"]["run_id"]

    data = await _wait_reviewing(client, run_id)
    assert data["status"] == "reviewing"  # 有节点待审核时 run 状态暴露为 reviewing
    assert data["finished_at"] is None  # 挂起不算结束
    assert data["error"] is None  # 挂起不算失败
    assert data["nodes"]["review"]["status"] == "reviewing"
    assert "merge" in data["nodes"]["review"]["payload"]  # 审核卡片展示的待审 payload
    assert data["nodes"]["fetch"]["status"] == "completed"

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.json()["data"] == {"status": "ok", "run_id": run_id, "node": "review", "approve": True}

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["publish"]["status"] == "completed"
    assert data["nodes"]["merge"]["status"] == "completed"
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
    data = body["data"]
    runs = data["items"]
    assert data["total"] == 2 and data["offset"] == 0 and data["limit"] == 50
    assert [r["id"] for r in runs] == [second, first]
    fields = set(runs[0])
    assert fields == {"id", "name", "created_at", "finished_at", "status", "error"}
    assert "T" not in runs[0]["created_at"]  # 响应层日期用空格分隔


async def test_list_runs_pagination(client: AsyncClient) -> None:
    """offset/limit 分页：只取本页、total 为全局总数、越界返回空页。"""
    for i in range(3):
        record = RunRecord(
            name=f"p{i}",
            config_file="pipeline.yaml",
            mermaid="graph TD\n",
            created_at=f"2026-01-0{i+1}T00:00:00",
            status="completed",
            nodes={},
        )
        await run_service.save(record)

    resp = await client.get("/api/v1/runs", params={"offset": 1, "limit": 1})
    data = resp.json()["data"]
    assert data["total"] == 3
    assert [r["id"] for r in data["items"]] == [2]  # 最新在前：第 2 页 = id 2
    assert data["offset"] == 1 and data["limit"] == 1

    resp = await client.get("/api/v1/runs", params={"offset": 9})
    data = resp.json()["data"]
    assert data["items"] == [] and data["total"] == 3

    resp = await client.get("/api/v1/runs", params={"limit": 0})
    assert resp.status_code == 422


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
    monkeypatch.setattr(orchestrator.settings, "PIPELINES_DIR", tmp_path)

    resp = await client.post("/api/v1/runs", json={"config_file": "bad.yaml"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400
    assert "no_such_fn" in body["msg"]

    # 配置错误不产生垃圾 run 记录
    resp = await client.get("/api/v1/runs")
    assert resp.json()["data"]["items"] == []


async def test_create_run_with_config_file(client: AsyncClient) -> None:
    """POST /runs 带 config_file：跑指定的 demo 流水线并持久化文件名。"""
    resp = await client.post("/api/v1/runs", json={"config_file": "01_basic_chain.yaml"})
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["config_file"] == "01_basic_chain.yaml"
    assert all(n["status"] != "reviewing" for n in data["nodes"].values())  # 无人工审核节点
    assert data["nodes"]["report"]["status"] == "completed"


async def test_create_run_unknown_config_400(client: AsyncClient) -> None:
    """未知 config_file 拒绝创建，且不产生垃圾 run 记录。"""
    resp = await client.post("/api/v1/runs", json={"config_file": "no_such.yaml"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 400
    assert "未知的流水线配置" in body["msg"]

    resp = await client.get("/api/v1/runs")
    assert resp.json()["data"]["items"] == []


async def test_resume_stuck_run_alternate_config(client: AsyncClient) -> None:
    """重启恢复也按 config_file 找对应的 YAML（非主演示流水线）。"""
    record = RunRecord(
        name="基础链路",
        config_file="01_basic_chain.yaml",
        mermaid="graph TD\n",
        created_at="2026-01-01T00:00:00",
        status="running",
        nodes={},
    )
    await run_service.save(record)
    run_id = record.id
    assert run_id is not None

    await orchestrator.resume_stuck_runs()

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["config_file"] == "01_basic_chain.yaml"


async def test_resume_stuck_run(client: AsyncClient) -> None:
    """模拟崩溃重启：running 记录 + 部分节点快照 → resume 续跑。"""
    record = RunRecord(
        name="content_pipeline",
        config_file="05_human_review.yaml",
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
    assert data["nodes"]["review"]["status"] == "reviewing"  # 05_human_review 有人工审核节点
    assert data["nodes"]["fetch"]["status"] == "completed"  # 已完成节点不重跑

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.status_code == 200

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["publish"]["status"] == "completed"


async def test_resume_suspended_run_re_suspends(client: AsyncClient) -> None:
    """挂起中的 run 被再次 resume（无新决策）：幂等重挂起，审批后照常完成。"""
    resp = await client.post("/api/v1/runs")
    run_id = resp.json()["data"]["run_id"]
    await _wait_reviewing(client, run_id)

    await asyncio.sleep(0.1)  # 首个 run 任务挂起退出（REVIEWING 已落库）
    await orchestrator.resume_stuck_runs()  # 模拟第二次重启

    await asyncio.sleep(0.1)  # 续跑任务无决策可消费 → 幂等重挂起退出
    data = (await client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert data["status"] == "reviewing"  # 重挂起后仍是"等待审核"状态
    assert data["nodes"]["review"]["status"] == "reviewing"

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.status_code == 200
    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["publish"]["status"] == "completed"
