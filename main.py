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

PIPELINE = Path(__file__).parent / "pipeline.yaml"
INDEX = Path(__file__).parent / "index.html"


# ---------------------------------------------------------------------------
# Run plumbing
# ---------------------------------------------------------------------------

def _make_approver(record: database.RunRecord) -> ApproverFunc:
    """Build the approver for one run: 在节点条目上标记待审并落库，然后
    轮询决策表直到 /api/approve 写入决策。"""
    async def approver(node_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record.nodes.setdefault(node_name, {})["pending"] = True
        await database.save(record)
        while True:
            await asyncio.sleep(0.3)
            decision = await database.take_decision(record.id, node_name)
            if decision is not None:
                record.nodes[node_name].pop("pending", None)
                await database.save(record)
                return decision
    return approver


async def _run_pipeline(record: database.RunRecord, dag: DAG) -> None:
    def on_event(result: NodeResult) -> None:
        record.nodes[result.node_name] = result.to_dict()
        asyncio.create_task(database.save(record))

    try:
        await dag.run(on_event=on_event)
    except asyncio.CancelledError:
        record.status = "cancelled"
        raise
    except DAGExecutionError as exc:
        record.error = str(exc)
        for name, result in exc.results.items():
            record.nodes[name] = result.to_dict()
    except Exception as exc:  # unexpected — surface it in the UI instead of dying
        record.error = f"{type(exc).__name__}: {exc}"
    finally:
        record.finished_at = datetime.now().isoformat(timespec="seconds")
        record.refresh_status()
        await database.save(record)


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
    record = database.RunRecord(
        name=PIPELINE.name,       # 占位：任务加载 dag 前没有真实名字
        config_file=PIPELINE.name,  # yaml 文件名
        mermaid="",               # 占位：加载后更新
        created_at=datetime.now().isoformat(timespec="seconds"),
        status="running",
    )
    record = await database.save(record)
    try:
        dag = load_dag(PIPELINE, functions=FUNCTIONS, approver=_make_approver(record))
    except ValueError as exc:
        record.status = "failed"
        record.error = str(exc)
        await database.save(record)
        return JSONResponse({"error": str(exc)}, status_code=400)
    record.name = dag.name
    record.mermaid = dag.to_mermaid()
    await database.save(record)
    asyncio.create_task(_run_pipeline(record, dag))
    return {"run_id": record.id}


@app.get("/api/runs")
async def api_runs():
    # 数据库是唯一真相（活跃 run 的进度也已按事件落库）
    records = (await database.load()).values()
    fields = ("id", "name", "created_at", "finished_at", "status", "error")
    items = sorted(records, key=lambda s: s.created_at or "", reverse=True)
    return {"runs": [{k: getattr(s, k) for k in fields} for s in items]}


@app.get("/api/state/{run_id}")
async def api_state(run_id: int):
    record = await database.get(run_id)
    if record is None:
        return JSONResponse({"error": f"Unknown run {run_id!r}"}, status_code=404)
    return {
        **record.model_dump(),
        "pending": sorted(
            n for n, e in record.nodes.items()
            if isinstance(e, dict) and e.get("pending")
        ),
    }


@app.post("/api/approve/{run_id}/{node_name}")
async def api_approve(run_id: int, node_name: str, body: ApproveBody):
    record = await database.get(run_id)
    entry = (record.nodes or {}).get(node_name) if record else None
    if not isinstance(entry, dict) or not entry.get("pending"):
        return JSONResponse(
            {"error": f"No pending review for node {node_name!r}"}, status_code=404
        )
    await database.save_decision(
        run_id, node_name, {"approve": body.approve, "reason": body.reason}
    )
    return {"status": "ok", "run_id": run_id, "node": node_name, "approve": body.approve}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(INDEX)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
