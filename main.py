"""
Web UI for DAG Flow — execution monitoring + web-based human review.

Orchestration stays in YAML (pipeline.yaml). The server supports
multiple concurrent runs, keeps execution history in a JSON file
(survives restarts), and records every human review decision.
The frontend lives in index.html.

Run::

    python web.py          # then open http://127.0.0.1:8000
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from config import load_dag
from dag import DAG
from demo_functions import FUNCTIONS
from executor import Execution

PIPELINE = Path(__file__).parent / "pipeline.yaml"
HISTORY_FILE = Path(__file__).parent / "runs_history.json"
INDEX = Path(__file__).parent / "index.html"


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------

class _Run:
    def __init__(self, run_id: str) -> None:
        self.id = run_id
        self.started_at = datetime.now()  # created at (shown before execution starts)
        self.dag: Optional[DAG] = None
        self.execution: Optional[Execution] = None
        self.task: Optional[asyncio.Task] = None

    def snapshot(self) -> Dict[str, Any]:
        """JSON-serializable record of this run (also used for history).

        All execution state lives on :class:`Execution` — the single source
        of truth that the framework updates in place.
        """
        ex = self.execution
        return {
            "id": self.id,
            "name": self.dag.name if self.dag else None,
            "started_at": (
                ex.started_at if ex and ex.started_at else self.started_at
            ).isoformat(timespec="seconds"),
            "finished_at": ex.finished_at.isoformat(timespec="seconds")
            if ex and ex.finished_at else None,
            "running": ex.status == "running" if ex else False,
            "result": ex.status if ex else "pending",
            "error": ex.error if ex else None,
            "nodes": {
                name: {
                    "status": r.status.value,
                    "output": r.output,
                    "error": str(r.error) if r.error else None,
                    "attempts": r.attempts,
                    "duration_ms": round(r.duration_ms) if r.duration_ms else 0,
                }
                for name, r in ex.nodes.items()
            } if ex else {},
            "pending": {
                name: entry["payload"] for name, entry in ex.pending.items()
            } if ex else {},
            "mermaid": self.dag.to_mermaid() if self.dag else "",
        }


runs: Dict[str, _Run] = {}     # active _Run objects
history: Dict[str, Dict] = {}  # persisted snapshots (active + finished)


def _load_history() -> None:
    if HISTORY_FILE.exists():
        try:
            history.update(json.loads(HISTORY_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    # Runs that were active when the server died are interrupted, not running.
    for snap in history.values():
        if snap.get("running"):
            snap["running"] = False
            snap["result"] = "interrupted"


_load_history()


# ---------------------------------------------------------------------------
# Run plumbing
# ---------------------------------------------------------------------------

def _persist(run: _Run) -> None:
    """Record the run's final state (fired when its task completes)."""
    history[run.id] = run.snapshot()
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

app = FastAPI(title="DAG Flow")


class ApproveBody(BaseModel):
    approve: bool
    reason: Optional[str] = None


@app.post("/api/run")
async def api_run():
    run = _Run(datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4])
    try:
        run.dag = load_dag(PIPELINE, functions=FUNCTIONS)
        run.execution = run.dag.run()  # validates immediately
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    runs[run.id] = run
    run.task = run.execution.start()
    run.task.add_done_callback(lambda _task: _persist(run))
    # persist immediately so a crash mid-run still leaves a visible record
    history[run.id] = run.snapshot()
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"run_id": run.id}


@app.get("/api/runs")
async def api_runs():
    merged = {**history, **{rid: r.snapshot() for rid, r in runs.items()}}
    fields = ("id", "started_at", "finished_at", "running", "result", "error")
    items = sorted(merged.values(), key=lambda s: s["started_at"], reverse=True)
    return {"runs": [{k: s[k] for k in fields} for s in items]}


@app.get("/api/state/{run_id}")
async def api_state(run_id: str):
    run = runs.get(run_id)
    snap = run.snapshot() if run is not None else history.get(run_id)
    if snap is None:
        return JSONResponse({"error": f"Unknown run {run_id!r}"}, status_code=404)
    return snap


@app.post("/api/approve/{run_id}/{node_name}")
async def api_approve(run_id: str, node_name: str, body: ApproveBody):
    run = runs.get(run_id)
    if run is None or run.execution is None:
        return JSONResponse({"error": f"No active run {run_id!r}"}, status_code=404)
    if not run.execution.resolve_review(
        node_name, {"approve": body.approve, "reason": body.reason}
    ):
        return JSONResponse(
            {"error": f"No pending review for node {node_name!r}"}, status_code=404
        )
    # persist state even if the server dies before the run finishes
    history[run.id] = run.snapshot()
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"status": "ok", "run_id": run_id, "node": node_name, "approve": body.approve}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(INDEX)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
