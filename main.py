"""
Web UI for DAG Flow — execution monitoring + web-based human review.

Orchestration stays in YAML (pipeline.yaml). The server supports
multiple concurrent runs, keeps execution history in PostgreSQL via
SQLModel (DATABASE_URL, survives restarts), and records every human
review decision. The frontend lives in index.html.

Run::

    python main.py          # then open http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import database
from config import load_dag
from demo_functions import FUNCTIONS
from executor import DAGExecutionError
from node import ApproverFunc
from schems import NodeResult

# psycopg 异步驱动不支持 Windows 默认的 ProactorEventLoop；SelectorEventLoop
# 对本应用（无子进程）无副作用。必须在任何事件循环创建之前设置。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PIPELINE = Path(__file__).parent / "pipeline.yaml"
INDEX = Path(__file__).parent / "index.html"


# ---------------------------------------------------------------------------
# Run state
#
# RunRecord 是唯一的 run 状态载体（同时是快照、API 返回值和数据库行）；
# 装不下的内存态（后台任务、挂起的 future）放两个按 run_id 索引的字典。
# ---------------------------------------------------------------------------

runs: Dict[str, database.RunRecord] = {}            # 活跃 run 的记录（id -> 记录）
tasks: Dict[str, asyncio.Task] = {}                 # id -> 后台执行任务
reviews: Dict[str, Dict[str, asyncio.Future]] = {}  # run_id -> {node: 挂起的 future}


def _node_dict(result: NodeResult) -> Dict[str, Any]:
    """NodeResult -> JSON-safe dict (the shape the frontend expects)."""
    return {
        "status": result.status.value,
        "output": result.output,
        "error": str(result.error) if result.error else None,
        "attempts": result.attempts,
        "duration_ms": round(result.duration_ms) if result.duration_ms else 0,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

async def _persist(run_id: str) -> None:
    """Refresh derived fields and save the record."""
    record = runs.get(run_id)
    if record is None:
        return  # run 已不在内存 — 无需持久化
    if record.result != "interrupted":  # 显式标记（如取消）不覆盖
        if record.error:
            record.result = "failed"
        elif record.finished_at:
            record.result = "completed"
        else:
            record.result = "running"
    record.running = record.finished_at is None
    await database.save(record)


# ---------------------------------------------------------------------------
# Run plumbing
# ---------------------------------------------------------------------------

def _make_approver(run_id: str) -> ApproverFunc:
    """Build the approver for one run: payload goes into the record (UI 轮询
    可见)，future 放进 reviews，暂停直到 /api/approve 答复。"""
    async def approver(node_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = runs[run_id]
        future = asyncio.get_running_loop().create_future()
        record.pending[node_name] = payload
        reviews[run_id][node_name] = future
        try:
            return await future
        finally:
            reviews[run_id].pop(node_name, None)
            record.pending.pop(node_name, None)  # resolved or cancelled — no stale entry
    return approver


async def _run_pipeline(run_id: str) -> None:
    record = runs[run_id]

    def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = _node_dict(result)

    try:
        dag = load_dag(PIPELINE, functions=FUNCTIONS, approver=_make_approver(run_id))
        await dag.run(on_event=on_event)
    except asyncio.CancelledError:
        record.result = "interrupted"  # 中途取消 ≠ 完成
        raise
    except DAGExecutionError as exc:
        record.error = str(exc)
        for name, result in exc.results.items():
            record.nodes[name] = _node_dict(result)
    except Exception as exc:  # unexpected — surface it in the UI instead of dying
        record.error = f"{type(exc).__name__}: {exc}"
    finally:
        record.finished_at = datetime.now().isoformat(timespec="seconds")
        await _persist(run_id)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init()
    # 上次进程退出时仍在 running 的记录标记为 interrupted
    for record in (await database.load()).values():
        if record.running:
            record.running = False
            record.result = "interrupted"
            await database.save(record)
    yield


app = FastAPI(title="DAG Flow", lifespan=lifespan)


class ApproveBody(BaseModel):
    approve: bool
    reason: Optional[str] = None


@app.post("/api/run")
async def api_run():
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    record = database.RunRecord(
        id=run_id,
        name=PIPELINE.name,
        started_at=datetime.now().isoformat(timespec="seconds"),
        running=True,
        result="running",
    )
    runs[run_id] = record
    reviews[run_id] = {}
    tasks[run_id] = asyncio.create_task(_run_pipeline(run_id))
    # persist immediately so a crash mid-run still leaves a visible record
    await database.save(record)
    return {"run_id": run_id}


@app.get("/api/runs")
async def api_runs():
    # 数据库为历史真相；活跃 run 的记录覆盖同名键（内容更新）
    merged = {**await database.load(), **runs}
    fields = ("id", "started_at", "finished_at", "running", "result", "error")
    items = sorted(merged.values(), key=lambda s: s.started_at or "", reverse=True)
    return {"runs": [{k: getattr(s, k) for k in fields} for s in items]}


@app.get("/api/state/{run_id}")
async def api_state(run_id: str):
    record = runs.get(run_id)
    if record is None:
        record = await database.get(run_id)
    if record is None:
        return JSONResponse({"error": f"Unknown run {run_id!r}"}, status_code=404)
    return record


@app.post("/api/approve/{run_id}/{node_name}")
async def api_approve(run_id: str, node_name: str, body: ApproveBody):
    future = reviews.get(run_id, {}).get(node_name)
    if future is None:
        return JSONResponse(
            {"error": f"No pending review for node {node_name!r}"}, status_code=404
        )
    future.set_result({"approve": body.approve, "reason": body.reason})
    reviews[run_id].pop(node_name, None)
    runs[run_id].pending.pop(node_name, None)
    # persist state even if the server dies before the run finishes
    await _persist(run_id)
    return {"status": "ok", "run_id": run_id, "node": node_name, "approve": body.approve}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(INDEX)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
