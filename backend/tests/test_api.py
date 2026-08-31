"""API 集成测试 — run 生命周期 + 审批流 + 错误信封 + 重启恢复"""

import asyncio
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlmodel import select

from app.core import database
from app.core.config import settings
from app.models.review import ReviewDecision
from app.models.run import RunRecord
from app.services import orchestrator
from app.services import runs as run_service
from app.utils.files import save_upload

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
    """轮询 run 直到挂起落库（run 状态 reviewing）。

    等 run 级状态而非节点级：节点 reviewing 快照先于状态落库（emit →
    CAS 两步），等节点会拿到 run 仍 running 的中间态。状态翻转时节点
    快照必然已就位。
    """

    async def poll() -> dict[str, Any]:
        while True:
            resp = await client.get(f"/api/v1/runs/{run_id}")
            data = resp.json()["data"]
            if data["status"] == "reviewing":
                return data
            if data["status"] != "running":
                raise AssertionError(f"run 在审批前已结束: {data}")
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def _wait_node_reviewing(
    client: AsyncClient, run_id: int, node: str, timeout: float = 15
) -> dict[str, Any]:
    """轮询 run 直到指定节点进入待审批（多级审核时上一下快照可能仍是
    reviewing，等"任意节点"会竞态，必须等目标节点自己的快照就位）。"""

    async def poll() -> dict[str, Any]:
        while True:
            resp = await client.get(f"/api/v1/runs/{run_id}")
            data = resp.json()["data"]
            if data["nodes"].get(node, {}).get("status") == "reviewing":
                return data
            if data["status"] not in ("running", "reviewing"):
                raise AssertionError(f"run 在 {node} 审批前已结束: {data}")
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def _wait_run_status(
    client: AsyncClient, run_id: int, status: str, timeout: float = 15
) -> dict[str, Any]:
    """轮询 run 直到指定的 run 级状态。

    重启恢复后区分旧/新快照的可靠标记：resume 同步置 running，重挂起才
    回 reviewing —— 观察到 reviewing 必然是重放重建后的快照（node 条目
    的 reviewing 可能还是旧的，不可作分界）。
    """

    async def poll() -> dict[str, Any]:
        while True:
            resp = await client.get(f"/api/v1/runs/{run_id}")
            data = resp.json()["data"]
            if data["status"] == status:
                return data
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


def _store_upload(filename: str, text: str = "第一段设定。\n\n第二段设定。") -> str:
    """把文本按上传文件落盘（进 conftest 重定向的临时目录），返回存储 id。"""
    return save_upload(text.encode(), Path(filename).suffix.lower())


async def _create_and_approve(client: AsyncClient, approve: bool = True) -> int:
    """建一个 run 并走完审批流，返回 run_id。"""
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "05_human_review.yaml", "inputs": {"title": "测试标题", "content": "测试正文"}},
    )
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
        "svc_external_api",
        "demo_tick",
        "demo_keep_iterating",
        "demo_needs_review",
    }
    assert expected <= names
    assert set(types[0]) == {"name", "label", "description"}

    # Verify specific node types exist
    node_names = [t["name"] for t in types]
    assert "cfg_needs_report" in node_names
    assert "demo_keep_iterating" in node_names
    assert "demo_needs_review" in node_names
    assert "intent_is" in node_names
    assert "notify_long_body" in node_names
    assert "notify_message" in node_names

    notify = {t["name"]: t for t in types}["notify_message"]  # 插件类型随目录自动出现
    assert notify["label"] == "生成通知"


async def test_run_lifecycle_approve(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "05_human_review.yaml", "inputs": {"title": "测试标题", "content": "测试正文"}},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == 201 and body["msg"] == "ok"
    run_id = body["data"]["run_id"]

    data = await _wait_reviewing(client, run_id)
    assert data["status"] == "reviewing"  # 有节点待审核时 run 状态暴露为 reviewing
    assert data["finished_at"] is None  # 挂起不算结束
    assert data["error"] is None  # 挂起不算失败
    assert data["nodes"]["review"]["status"] == "reviewing"
    assert "title" in data["nodes"]["review"]["output"]["payload"]  # 审核卡片展示的待审 payload（来自 params）
    assert "content" in data["nodes"]["review"]["output"]["payload"]

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.json()["data"] == {"status": "ok", "run_id": run_id, "node": "review", "approve": True}

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["publish"]["status"] == "completed"
    assert data["nodes"]["review"]["status"] == "completed"
    assert data["error"] is None


async def test_run_lifecycle_reject(client: AsyncClient) -> None:
    run_id = await _create_and_approve(client, approve=False)
    resp = await client.get(f"/api/v1/runs/{run_id}")
    data = resp.json()["data"]
    assert data["status"] == "failed"
    assert data["nodes"]["review"]["status"] == "failed"
    assert data["nodes"]["review"]["error"] == "Rejected in test"
    assert data["nodes"]["publish"]["status"] == "upstream_failed"
    # run-error 摘要带失败原因（节点名 + 异常类型 + 消息）
    assert "review" in data["error"]
    assert "HumanRejected: Rejected in test" in data["error"]


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
    assert fields == {"id", "name", "created_at", "finished_at", "status", "error", "config_file"}
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


async def test_list_runs_filters(client: AsyncClient) -> None:
    """status/config_file 筛选：total 为筛选后总数；summary 为全局计数，
    不随筛选变化（前端轮询/电流据此判断，筛过的视图不能停摆）。"""
    seeds = [
        ("completed", "01_serial.yaml"),
        ("failed", "01_serial.yaml"),
        ("running", "02_condition_branching.yaml"),
        ("reviewing", "05_human_review.yaml"),
    ]
    for i, (status, config) in enumerate(seeds):
        await run_service.save(
            RunRecord(
                name=f"f{i}",
                config_file=config,
                mermaid="graph TD\n",
                created_at=f"2026-02-0{i + 1}T00:00:00",
                status=status,
                nodes={},
            )
        )

    # 状态筛选 + 全局 summary（running=1，非终态=running+reviewing=2）
    resp = await client.get("/api/v1/runs", params={"status": "completed"})
    data = resp.json()["data"]
    assert data["total"] == 1
    assert [r["status"] for r in data["items"]] == ["completed"]
    assert data["summary"] == {"running": 1, "active": 2}

    # 流水线筛选
    resp = await client.get("/api/v1/runs", params={"config_file": "01_serial.yaml"})
    data = resp.json()["data"]
    assert data["total"] == 2
    assert all(r["config_file"] == "01_serial.yaml" for r in data["items"])

    # 组合筛选
    resp = await client.get(
        "/api/v1/runs", params={"status": "failed", "config_file": "01_serial.yaml"}
    )
    data = resp.json()["data"]
    assert data["total"] == 1 and data["items"][0]["status"] == "failed"

    # 无效状态枚举 422
    resp = await client.get("/api/v1/runs", params={"status": "bogus"})
    assert resp.status_code == 422

    # 筛选无结果：空页但 summary 仍是全局值
    resp = await client.get("/api/v1/runs", params={"config_file": "no_such.yaml"})
    data = resp.json()["data"]
    assert data["items"] == [] and data["total"] == 0
    assert data["summary"]["active"] == 2


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
    doc = (
        await client.post(
            "/api/v1/uploads",
            files={"file": ("北境要塞.md", "第一段设定。\n\n第二段设定。".encode(), "text/markdown")},
        )
    ).json()["data"]
    resp = await client.post(
        "/api/v1/runs",
        json={
            "config_file": "01_serial.yaml",
            # file 参数传上传接口返回的 {id, filename} 引用（rag_load 读盘解析）
            "inputs": {"document": {"id": doc["id"], "filename": doc["filename"]}},
        },
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["config_file"] == "01_serial.yaml"
    assert all(n["status"] != "reviewing" for n in data["nodes"].values())  # 无人工审核节点
    assert data["nodes"]["upsert"]["status"] == "completed"


async def _wait_status(client: AsyncClient, run_id: int, status: str, timeout: float = 15) -> dict[str, Any]:
    """轮询 run 直到出现指定状态（如挂起到 reviewing）。"""
    async def poll() -> dict[str, Any]:
        while True:
            data = (await client.get(f"/api/v1/runs/{run_id}")).json()["data"]
            if data["status"] == status:
                return data
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def _count_decisions(run_id: int) -> int:
    """该 run 的审批决策行数（无外键，删除靠服务层级联）。"""
    async with database.AsyncSessionLocal() as session:
        rows = (await session.exec(select(ReviewDecision).where(ReviewDecision.run_id == run_id))).all()
        return len(rows)


async def test_delete_run(client: AsyncClient) -> None:
    """DELETE /runs/{id}：终态可删（审批决策级联清理）；待审核拒绝；不存在 404。"""
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "05_human_review.yaml", "inputs": {"title": "测试标题", "content": "测试正文"}},
    )
    rid = resp.json()["data"]["run_id"]
    await _wait_status(client, rid, "reviewing")

    # 待审核（非终态）拒绝删除
    resp = await client.delete(f"/api/v1/runs/{rid}")
    assert resp.status_code == 400
    assert "仅终态记录可删除" in resp.json()["msg"]
    assert (await client.get(f"/api/v1/runs/{rid}")).status_code == 200  # 记录还在

    # 审批通过 → 终态后可删，决策（审计痕迹）随之清理
    resp = await client.post(f"/api/v1/runs/{rid}/approve/review", json={"approve": True})
    assert resp.status_code == 200
    assert (await _wait_terminal(client, rid))["status"] == "completed"
    assert await _count_decisions(rid) == 1

    resp = await client.delete(f"/api/v1/runs/{rid}")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"deleted": rid}
    assert (await client.get(f"/api/v1/runs/{rid}")).status_code == 404
    assert await _count_decisions(rid) == 0

    # 不存在 404
    assert (await client.delete("/api/v1/runs/99999")).status_code == 404


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
    """重启恢复按钉住的 definition 续跑（非主演示流水线）。"""
    record = RunRecord(
        name="基础链路",
        config_file="01_serial.yaml",
        definition=(settings.PIPELINES_DIR / "01_serial.yaml").read_text(encoding="utf-8"),
        mermaid="graph TD\n",
        created_at="2026-01-01T00:00:00",
        status="running",
        nodes={},
        inputs={"document": {"id": _store_upload("北境要塞.md"), "filename": "北境要塞.md"}},
    )
    await run_service.save(record)
    run_id = record.id
    assert run_id is not None

    await orchestrator.resume_stuck_runs()

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["config_file"] == "01_serial.yaml"


async def test_resume_stuck_run(client: AsyncClient) -> None:
    """模拟崩溃重启：running 记录（含定义快照）→ 按钉住的定义 resume 续跑。"""
    record = RunRecord(
        name="content_pipeline",
        config_file="05_human_review.yaml",
        definition=(settings.PIPELINES_DIR / "05_human_review.yaml").read_text(encoding="utf-8"),
        mermaid="graph TD\n",
        created_at="2026-01-01T00:00:00",
        status="running",
        nodes={
            # 新版 05 无 fetch 节点，直接用输入参数；空 nodes 表示刚开始执行
        },
        inputs={"title": "恢复测试", "content": "崩溃前的正文"},
    )
    await run_service.save(record)
    run_id = record.id
    assert run_id is not None

    await orchestrator.resume_stuck_runs()

    data = await _wait_reviewing(client, run_id)
    assert data["nodes"]["review"]["status"] == "reviewing"  # 05_human_review 有人工审核节点

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.status_code == 200

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["publish"]["status"] == "completed"


async def test_resume_suspended_run_re_suspends(client: AsyncClient) -> None:
    """挂起中的 run 被再次 resume（无新决策）：幂等重挂起，审批后照常完成。"""
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "05_human_review.yaml", "inputs": {"title": "重复恢复", "content": "测试正文"}},
    )
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


# ---------------------------------------------------------------------------
# 运行时输入 inputs
# ---------------------------------------------------------------------------


async def test_create_run_with_inputs(client: AsyncClient, monkeypatch, tmp_path) -> None:
    """带 inputs 创建：值进入共享上下文（demo_tick 在 tick 基础上 +1），
    同名键运行时优先于 YAML 默认值；详情回显输入快照。"""
    pipe = tmp_path / "inputs_demo.yaml"
    pipe.write_text(
        "name: inputs_demo\n"
        "params:\n"
        "  tick:\n"
        "    default: 1\n"
        "nodes:\n"
        "  counter:\n"
        "    type: demo_tick\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator.settings, "PIPELINES_DIR", tmp_path)

    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "inputs_demo.yaml", "inputs": {"tick": 41}},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["inputs"] == {"tick": 41}  # 运行时输入快照回显
    assert data["nodes"]["counter"]["output"] == 42  # 覆盖了 YAML 默认 tick=1


async def test_inputs_yaml_default_kept_when_not_overridden(client: AsyncClient, monkeypatch, tmp_path) -> None:
    """运行时未覆盖的键沿用 YAML 默认值：不带 inputs 创建，tick 仍取 YAML 的 1。"""
    pipe = tmp_path / "inputs_demo.yaml"
    pipe.write_text(
        "name: inputs_demo\n"
        "params:\n"
        "  tick:\n"
        "    default: 1\n"
        "nodes:\n"
        "  counter:\n"
        "    type: demo_tick\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator.settings, "PIPELINES_DIR", tmp_path)

    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "inputs_demo.yaml"},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["counter"]["output"] == 2  # YAML 默认 tick=1 生效
    assert data["inputs"] == {"tick": 1}  # 生效输入快照含 YAML 默认值，run 自描述


async def test_inputs_survive_review_resume(client: AsyncClient, monkeypatch, tmp_path) -> None:
    """审批挂起 → /approve 走 resume_record 重新 load_dag 续跑：
    record.inputs 回放进上下文，下游节点仍能读到运行时输入。"""
    pipe = tmp_path / "inputs_review.yaml"
    pipe.write_text(
        "name: inputs_review\n"
        "params:\n"
        "  tick:\n"
        "    label: 计数\n"
        "nodes:\n"
        "  review:\n"
        "    kind: human\n"
        '    prompt: "请审核"\n'
        "  counter:\n"
        "    type: demo_tick\n"
        "    depends_on: [review]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator.settings, "PIPELINES_DIR", tmp_path)

    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "inputs_review.yaml", "inputs": {"tick": 6}},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    await _wait_reviewing(client, run_id)
    data = (await client.get(f"/api/v1/runs/{run_id}")).json()["data"]
    assert data["nodes"]["review"]["output"]["payload"]["tick"] == 6  # 输入也进审核卡片 payload

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.status_code == 200

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["counter"]["output"] == 7  # resume 后 tick=6 仍在上下文


async def test_inputs_restart_resume_replays_inputs(client: AsyncClient, monkeypatch, tmp_path) -> None:
    """重启恢复（resume_stuck_runs）同样回放 inputs：reviewing 记录带输入快照。"""
    pipe = tmp_path / "inputs_review.yaml"
    pipe.write_text(
        "name: inputs_review\n"
        "params:\n"
        "  tick:\n"
        "    label: 计数\n"
        "nodes:\n"
        "  review:\n"
        "    kind: human\n"
        '    prompt: "请审核"\n'
        "  counter:\n"
        "    type: demo_tick\n"
        "    depends_on: [review]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator.settings, "PIPELINES_DIR", tmp_path)

    record = RunRecord(
        name="inputs_review",
        config_file="inputs_review.yaml",
        definition=pipe.read_text(encoding="utf-8"),
        mermaid="graph TD\n",
        created_at="2026-01-01T00:00:00",
        status="reviewing",
        nodes={"review": {"status": "reviewing", "payload": {"tick": 6}}},
        inputs={"tick": 6},
    )
    await run_service.save(record)
    run_id = record.id
    assert run_id is not None

    await orchestrator.resume_stuck_runs()  # 无决策 → 幂等重挂起
    await asyncio.sleep(0.1)

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/review", json={"approve": True})
    assert resp.status_code == 200
    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["counter"]["output"] == 7  # 重启恢复路径也回放 inputs


async def test_create_run_inputs_clash_node_name_400(client: AsyncClient) -> None:
    """输入键必须是 YAML params 中声明的参数，否则 400 且不产生 run 记录。"""
    resp = await client.post(
        "/api/v1/runs", json={"config_file": "05_human_review.yaml", "inputs": {"review": 1}}
    )
    assert resp.status_code == 400
    assert "未声明的参数键" in resp.json()["msg"]

    resp = await client.get("/api/v1/runs")
    assert resp.json()["data"]["items"] == []


async def test_loop_pipeline_completes_with_inputs(client: AsyncClient) -> None:
    """loop 流水线经 API 跑到终态：loop 输出快照可落库（回归：曾因输出
    自引用序列化失败卡死 running），且运行时输入进入循环累积。"""
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "04_loop_iteration.yaml", "inputs": {"tick": 6}},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["batch"]["status"] == "completed"
    # 运行时 tick=6 打底，3 轮迭代各 +1 → 累积到 9；快照已正常落库
    assert data["nodes"]["batch"]["output"] == {"tick": 9}


async def test_dual_review_with_required_title_content(client: AsyncClient) -> None:
    """08 两级审核：title/content 为必填输入（无默认值）；初审就地修订标题，
    终审卡片与发布看到的都是修订版（修订写回上下文，而非只进初审决策记录）。"""
    resp = await client.post(
        "/api/v1/runs",
        json={
            "config_file": "08_dual_review.yaml",
            "inputs": {"title": "自定义标题", "content": "自定义正文。"},
        },
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    # 初审
    data = await _wait_node_reviewing(client, run_id, "编辑初审")
    payload = data["nodes"]["编辑初审"]["output"]["payload"]
    # 声明式审核视图：payload 只含声明键 + 两个保留键
    #（_prompt=把关指引、_review=字段标签富映射，均置前）
    assert set(payload) == {"_prompt", "_review", "title", "content"}
    assert payload["_review"] == {"title": {"label": "标题"}, "content": {"label": "正文"}}
    assert payload["_prompt"]
    assert payload["title"] == "自定义标题"
    # 初审通过并就地修订标题（"改了再通过"）
    resp = await client.post(
        f"/api/v1/runs/{run_id}/approve/编辑初审",
        json={"approve": True, "edits": {"title": "修订后的标题"}},
    )
    assert resp.status_code == 200

    # 决策行自包含：审核时视图（原始标题）随决策入库，不依赖 run record 留档
    async with database.AsyncSessionLocal() as session:
        row = (
            await session.exec(
                select(ReviewDecision).where(
                    ReviewDecision.run_id == run_id, ReviewDecision.node_name == "编辑初审"
                )
            )
        ).one()
    assert row.payload["title"] == "自定义标题"
    assert row.edits == {"title": "修订后的标题"}

    # 终审
    data = await _wait_node_reviewing(client, run_id, "主编终审")
    assert data["nodes"]["主编终审"]["status"] == "reviewing"
    # 终审视图同样只有 title/content —— 一审的决策记录不进视图
    assert set(data["nodes"]["主编终审"]["output"]["payload"]) == {"_prompt", "_review", "title", "content"}
    # 修订已写回上下文：终审卡片显示初审修订后的标题，而非原始输入
    assert data["nodes"]["主编终审"]["output"]["payload"]["title"] == "修订后的标题"
    resp = await client.post(f"/api/v1/runs/{run_id}/approve/主编终审", json={"approve": True})
    assert resp.status_code == 200

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["发布"]["status"] == "completed"
    # 发布的也是修订版（cfg_publish 按审核视图键从共享上下文取生效值）
    assert "修订后的标题" in data["nodes"]["发布"]["output"]
    # 初审决策记录：payload 是审核时快照保持原样，修订留档 decision.edits
    first = data["nodes"]["编辑初审"]["output"]
    assert first["payload"]["title"] == "自定义标题"
    assert first["decision"]["edits"] == {"title": "修订后的标题"}


async def test_run_pins_definition_at_create(client: AsyncClient) -> None:
    """创建 run 时钉住定义原文：后续 resume 按它续跑，不读当前文件。"""
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "05_human_review.yaml", "inputs": {"title": "t", "content": "c"}},
    )
    assert resp.status_code == 201

    record = await run_service.get_run(resp.json()["data"]["run_id"])
    path = settings.PIPELINES_DIR / "05_human_review.yaml"
    assert record.definition == path.read_text(encoding="utf-8")


async def test_dual_review_edits_survive_restart(client: AsyncClient) -> None:
    """多级审核修订跨进程重启存活：初审改标题 → 崩溃重启（resume_stuck_runs
    恢复）→ 终审卡片与发布看到的仍是修订版。完成节点的修订写回由恢复路径
    重放（replay_review_edits），不依赖原进程内存。"""
    resp = await client.post(
        "/api/v1/runs",
        json={
            "config_file": "08_dual_review.yaml",
            "inputs": {"title": "重启前标题", "content": "重启前正文。"},
        },
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    # 初审通过并就地修订标题
    await _wait_node_reviewing(client, run_id, "编辑初审")
    resp = await client.post(
        f"/api/v1/runs/{run_id}/approve/编辑初审",
        json={"approve": True, "edits": {"title": "重启后仍生效的标题"}},
    )
    assert resp.status_code == 200
    await _wait_node_reviewing(client, run_id, "主编终审")

    await orchestrator.resume_stuck_runs()  # 模拟进程重启后的启动恢复

    # run 级 reviewing 必然是重放重建后的快照：修订经恢复重放，终审视图
    # 仍是修订后的标题（回归：曾退回显示原始输入）
    data = await _wait_run_status(client, run_id, "reviewing")
    assert data["nodes"]["主编终审"]["output"]["payload"]["title"] == "重启后仍生效的标题"

    resp = await client.post(f"/api/v1/runs/{run_id}/approve/主编终审", json={"approve": True})
    assert resp.status_code == 200

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert "重启后仍生效的标题" in data["nodes"]["发布"]["output"]


async def test_dual_review_missing_required_400(client: AsyncClient) -> None:
    """08 必填 title/content：缺任一 → 400 列出缺失键，不产生 run 记录。"""
    resp = await client.post("/api/v1/runs", json={"config_file": "08_dual_review.yaml"})
    assert resp.status_code == 400
    # 报错按声明顺序列出缺失键
    assert "title" in resp.json()["msg"] and "content" in resp.json()["msg"]

    resp = await client.post(
        "/api/v1/runs", json={"config_file": "08_dual_review.yaml", "inputs": {"title": "只有标题"}}
    )
    assert resp.status_code == 400
    assert "必填参数缺失或为空: content" in resp.json()["msg"]

    resp = await client.get("/api/v1/runs")
    assert resp.json()["data"]["items"] == []


async def test_required_inputs_missing_400(client: AsyncClient) -> None:
    """02 意图路由必填参数示例：不传 prompt → 400 列出缺的键，不产生 run 记录。"""
    resp = await client.post("/api/v1/runs", json={"config_file": "02_condition_branching.yaml"})
    assert resp.status_code == 400
    assert "必填参数缺失或为空: prompt" in resp.json()["msg"]

    resp = await client.get("/api/v1/runs")
    assert resp.json()["data"]["items"] == []


async def test_required_inputs_empty_rejected_400(client: AsyncClient) -> None:
    """02 必填 prompt：null/空串/空白串都算未提供 → 400，不产生 run 记录。"""
    for bad in ("", "   ", None):
        resp = await client.post(
            "/api/v1/runs",
            json={"config_file": "02_condition_branching.yaml", "inputs": {"prompt": bad}},
        )
        assert resp.status_code == 400, f"{bad!r} 应被拒绝"
        assert "必填参数缺失或为空: prompt" in resp.json()["msg"]

    resp = await client.get("/api/v1/runs")
    assert resp.json()["data"]["items"] == []


async def test_required_with_default_must_be_explicit_at_api(client: AsyncClient, monkeypatch, tmp_path) -> None:
    """必填+default：API 边界必填须显式——不传/空串/null → 400；
    显式提供才创建成功。（引擎层 run() 不传参时 default 顶班，
    见 test_declarative 的 test_required_with_default_fills_when_omitted。）"""
    pipe = tmp_path / "required_default.yaml"
    pipe.write_text(
        "name: required_default\n"
        "params:\n"
        "  tick:\n"
        "    required: true\n"
        "    default: 5\n"
        "nodes:\n"
        "  counter:\n"
        "    type: demo_tick\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator.settings, "PIPELINES_DIR", tmp_path)

    resp = await client.post("/api/v1/runs", json={"config_file": "required_default.yaml"})
    assert resp.status_code == 400  # default 不顶班
    assert "必填参数缺失或为空: tick" in resp.json()["msg"]

    for bad in ("", None):
        resp = await client.post(
            "/api/v1/runs", json={"config_file": "required_default.yaml", "inputs": {"tick": bad}}
        )
        assert resp.status_code == 400

    resp = await client.post(
        "/api/v1/runs", json={"config_file": "required_default.yaml", "inputs": {"tick": 5}}
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]

    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["inputs"] == {"tick": 5}  # 快照 = 可选默认 + 显式输入（必填必须显式）
    assert data["nodes"]["counter"]["output"] == 6


async def test_required_inputs_run_completes(client: AsyncClient) -> None:
    """02 带 prompt 运行：必填值进上下文，LLM 分类并走相应支路完成。"""
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "02_condition_branching.yaml", "inputs": {"prompt": "你好呀"}},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]
    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["classify"]["status"] == "completed"
    assert data["nodes"]["chat_reply"]["status"] == "completed"

    # 第二次运行：不同的 prompt，验证运行时输入正确进入上下文
    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "02_condition_branching.yaml", "inputs": {"prompt": "北境要塞是什么"}},
    )
    assert resp.status_code == 201
    run_id = resp.json()["data"]["run_id"]
    data = await _wait_terminal(client, run_id)
    assert data["status"] == "completed"
    assert data["nodes"]["classify"]["status"] == "completed"
    assert data["nodes"]["retrieve"]["status"] == "completed"
    assert data["nodes"]["rag_reply"]["status"] == "completed"
