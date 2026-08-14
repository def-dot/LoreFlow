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
from dag import DAG
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


#: config_file -> mermaid 源码（进程内缓存；api_state 按记录的 config_file 取图）
MERMAIDS: Dict[str, str] = {}


async def _noop_approver(node_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """占位 approver——只为构建 pipeline 图用，永远不会被调用。"""
    return {"approve": False}


def _mermaid(config_file: str) -> str:
    """返回 config_file 对应的图源码；未缓存则现场加载。"""
    if not config_file:
        return ""
    if config_file not in MERMAIDS:
        MERMAIDS[config_file] = load_dag(
            PIPELINE.parent / config_file,
            functions=FUNCTIONS,
            approver=_noop_approver,
        ).to_mermaid()
    return MERMAIDS[config_file]


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
    """Refresh derived status and save the record."""
    record = runs.get(run_id)
    if record is None:
        return  # run 已不在内存 — 无需持久化
    if record.status != "cancelled":  # 显式标记不覆盖
        if record.error:
            record.status = "failed"
        elif record.finished_at:
            record.status = "completed"
        else:
            record.status = "running"
    await database.save(record)


# ---------------------------------------------------------------------------
# Run plumbing
# ---------------------------------------------------------------------------

def _make_approver(run_id: str) -> ApproverFunc:
    """Build the approver for one run: 挂起 future 放进 reviews，暂停直到
    /api/approve 答复；待审内容由 UI 从 nodes 表读取。"""
    async def approver(node_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        future = asyncio.get_running_loop().create_future()
        reviews[run_id][node_name] = future
        try:
            return await future
        finally:
            reviews[run_id].pop(node_name, None)  # resolved or cancelled — no stale entry
    return approver


async def _run_pipeline(run_id: str, dag: DAG) -> None:
    record = runs[run_id]

    def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = _node_dict(result)

    try:
        await dag.run(on_event=on_event)
    except asyncio.CancelledError:
        record.status = "cancelled"  # 中途取消 ≠ 完成
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
    # 上次进程退出时仍在 running 的记录标记为 cancelled
    for record in (await database.load()).values():
        if record.status == "running":
            record.status = "cancelled"
            await database.save(record)
    yield


app = FastAPI(title="DAG Flow", lifespan=lifespan)


class ApproveBody(BaseModel):
    approve: bool
    reason: Optional[str] = None


@app.post("/api/run")
async def api_run():
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    try:
        dag = load_dag(PIPELINE, functions=FUNCTIONS, approver=_make_approver(run_id))
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    record = database.RunRecord(
        id=run_id,
        name=dag.name,             # yaml 里的 name 字段
        config_file=PIPELINE.name,  # yaml 文件名
        started_at=datetime.now().isoformat(timespec="seconds"),
        status="running",
    )
    runs[run_id] = record
    reviews[run_id] = {}
    tasks[run_id] = asyncio.create_task(_run_pipeline(run_id, dag))
    # persist immediately so a crash mid-run still leaves a visible record
    await database.save(record)
    return {"run_id": run_id}


@app.get("/api/runs")
async def api_runs():
    # 数据库为历史真相；活跃 run 的记录覆盖同名键（内容更新）
    merged = {**await database.load(), **runs}
    fields = ("id", "started_at", "finished_at", "status", "error")
    items = sorted(merged.values(), key=lambda s: s.started_at or "", reverse=True)
    return {"runs": [{k: getattr(s, k) for k in fields} for s in items]}


@app.get("/api/state/{run_id}")
async def api_state(run_id: str):
    record = runs.get(run_id)
    if record is None:
        record = await database.get(run_id)
    if record is None:
        return JSONResponse({"error": f"Unknown run {run_id!r}"}, status_code=404)
    return {
        **record.model_dump(),
        "pending": sorted(reviews.get(run_id, {})),  # 待审节点名（瞬态，不持久化）
        "mermaid": _mermaid(record.config_file),
    }


@app.post("/api/approve/{run_id}/{node_name}")
async def api_approve(run_id: str, node_name: str, body: ApproveBody):
    future = reviews.get(run_id, {}).get(node_name)
    if future is None:
        return JSONResponse(
            {"error": f"No pending review for node {node_name!r}"}, status_code=404
        )
    future.set_result({"approve": body.approve, "reason": body.reason})
    reviews[run_id].pop(node_name, None)
    # persist state even if the server dies before the run finishes
    await _persist(run_id)
    return {"status": "ok", "run_id": run_id, "node": node_name, "approve": body.approve}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(INDEX)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
