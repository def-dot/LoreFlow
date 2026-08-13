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
from typing import Any, Callable, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from config import load_dag
from dag import DAG
from demo_functions import FUNCTIONS
from executor import DAGExecutionError
from schems import NodeResult

PIPELINE = Path(__file__).parent / "pipeline.yaml"
HISTORY_FILE = Path(__file__).parent / "runs_history.json"
INDEX = Path(__file__).parent / "index.html"


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------

def _node_dict(r: NodeResult) -> Dict[str, Any]:
    return {
        "status": r.status.value,
        "output": r.output,
        "error": str(r.error) if r.error else None,
        "attempts": r.attempts,
        "duration_ms": round(r.duration_ms) if r.duration_ms else 0,
    }


class _Run:
    def __init__(self, run_id: str) -> None:
        self.id = run_id
        self.started_at = datetime.now()
        self.finished_at: Optional[datetime] = None
        self.dag: Optional[DAG] = None
        self.task: Optional[asyncio.Task] = None
        self.nodes: Dict[str, NodeResult] = {}
        self.pending: Dict[str, Dict[str, Any]] = {}
        self.decisions: list = []
        self.error: Optional[str] = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def snapshot(self) -> Dict[str, Any]:
        """JSON-serializable record of this run (also used for history)."""
        if self.error:
            result = "failed"
        elif self.finished_at:
            result = "completed"
        else:
            result = "running"
        return {
            "id": self.id,
            "name": self.dag.name if self.dag else None,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "finished_at": self.finished_at.isoformat(timespec="seconds")
            if self.finished_at else None,
            # finished_at is the source of truth: while the task's own finally
            # runs, task.done() is still False even though the run is over.
            "running": self.finished_at is None,
            "result": result,
            "error": self.error,
            "nodes": {name: _node_dict(r) for name, r in self.nodes.items()},
            "pending": {name: entry["payload"] for name, entry in self.pending.items()},
            "decisions": self.decisions,
            "mermaid": self.dag.to_mermaid() if self.dag else "",
        }


runs: Dict[str, _Run] = {}     # active _Run objects
history: Dict[str, Dict] = {}  # persisted snapshots (active + finished)


def _new_run_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


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


def _save_history() -> None:
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _record(run: _Run) -> None:
    """Put a run's snapshot into history and persist it."""
    history[run.id] = run.snapshot()
    _save_history()


_load_history()


# ---------------------------------------------------------------------------
# Run plumbing
# ---------------------------------------------------------------------------

def make_web_approver(run: _Run) -> Callable:
    """Review strategy for the web UI: pause until the browser decides."""

    async def approver(node_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        entry: Dict[str, Any] = {"payload": payload, "event": asyncio.Event(), "decision": None}
        run.pending[node_name] = entry
        await entry["event"].wait()
        return entry["decision"]

    return approver


def make_collector(run: _Run) -> Callable:
    """Collector for ``dag.run(on_event=...)``: latest state per node."""

    def on_event(result: NodeResult) -> None:
        run.nodes[result.node_name] = result

    return on_event


async def _run_pipeline(run: _Run) -> None:
    run.dag = load_dag(PIPELINE, functions=FUNCTIONS, approver=make_web_approver(run))
    try:
        await run.dag.run(on_event=make_collector(run))
    except DAGExecutionError as exc:
        run.error = str(exc)
        for name, result in exc.results.items():
            run.nodes[name] = result
    except Exception as exc:  # unexpected — surface it in the UI instead of dying
        run.error = f"{type(exc).__name__}: {exc}"
    finally:
        run.finished_at = datetime.now()
        _record(run)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

app = FastAPI(title="DAG Flow")


class ApproveBody(BaseModel):
    approve: bool
    reason: Optional[str] = None


@app.post("/api/run")
async def api_run():
    run = _Run(_new_run_id())
    runs[run.id] = run
    run.task = asyncio.create_task(_run_pipeline(run))
    _record(run)  # so a crash mid-run still leaves a visible (interrupted) record
    return {"run_id": run.id}


@app.get("/api/runs")
async def api_runs():
    merged = {**history, **{rid: r.snapshot() for rid, r in runs.items()}}
    fields = ("id", "started_at", "finished_at", "running", "result", "error")
    items = sorted(merged.values(), key=lambda s: s["started_at"], reverse=True)
    return {"runs": [{k: s[k] for k in fields} for s in items]}


def _get_snapshot(run_id: str) -> Optional[Dict[str, Any]]:
    run = runs.get(run_id)
    if run is not None:
        return run.snapshot()
    return history.get(run_id)


@app.get("/api/state/{run_id}")
async def api_state(run_id: str):
    snap = _get_snapshot(run_id)
    if snap is None:
        return JSONResponse({"error": f"Unknown run {run_id!r}"}, status_code=404)
    return snap


@app.post("/api/approve/{run_id}/{node_name}")
async def api_approve(run_id: str, node_name: str, body: ApproveBody):
    run = runs.get(run_id)
    if run is None:
        return JSONResponse({"error": f"No active run {run_id!r}"}, status_code=404)
    entry = run.pending.get(node_name)
    if entry is None:
        return JSONResponse(
            {"error": f"No pending review for node {node_name!r}"}, status_code=404
        )
    run.decisions.append({
        "node": node_name,
        "approve": body.approve,
        "reason": body.reason,
        "at": datetime.now().isoformat(timespec="seconds"),
    })
    entry["decision"] = {"approve": body.approve, "reason": body.reason}
    entry["event"].set()
    run.pending.pop(node_name, None)
    _record(run)  # audit trail survives even if the server dies before the run finishes
    return {"status": "ok", "run_id": run_id, "node": node_name, "approve": body.approve}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(INDEX)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
