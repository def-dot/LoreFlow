"""
Web UI for DAG Flow — execution monitoring + web-based human review.

Orchestration stays in YAML (pipeline.yaml). The server supports
multiple concurrent runs, keeps execution history in a JSON file
(survives restarts), and records every human review decision.

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
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from config import load_dag
from dag import DAG
from demo_functions import FUNCTIONS
from executor import DAGExecutionError
from schems import NodeResult

PIPELINE = Path(__file__).parent / "pipeline.yaml"
HISTORY_FILE = Path(__file__).parent / "runs_history.json"


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


# ---------------------------------------------------------------------------
# Single-page UI
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DAG Flow</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; font: 14px/1.5 system-ui, sans-serif; background: #14161a; color: #d8dce3; }
  header { padding: 12px 20px; border-bottom: 1px solid #2a2f38; display: flex; align-items: center; gap: 14px; }
  h1 { font-size: 18px; margin: 0; } h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: #8b93a1; margin: 0 0 8px; }
  button { font: inherit; padding: 5px 12px; border-radius: 6px; border: 1px solid #3a4150; background: #232833; color: #d8dce3; cursor: pointer; }
  button:hover:not(:disabled) { border-color: #5a6577; }
  button.ok { background: #14532d; border-color: #166534; }
  button.no { background: #7f1d1d; border-color: #991b1b; }
  .chip { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: #374151; white-space: nowrap; }
  .chip-completed { background: #14532d; } .chip-running { background: #1e40af; }
  .chip-failed { background: #7f1d1d; } .chip-interrupted { background: #92400e; }
  main { display: grid; grid-template-columns: 300px 1fr; gap: 18px; padding: 18px 20px; }
  aside { background: #1a1d23; border: 1px solid #2a2f38; border-radius: 10px; padding: 14px; align-self: start; }
  aside .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  #run-list { display: flex; flex-direction: column; gap: 6px; }
  .run-item { padding: 8px 10px; border: 1px solid #2a2f38; border-radius: 8px; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .run-item:hover { border-color: #5a6577; }
  .run-item.sel { border-color: #3b82f6; background: #1c2433; }
  .run-item .rid { font-size: 12px; color: #9aa4b2; }
  #detail-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }
  #run-error { color: #f87171; white-space: pre-wrap; flex-basis: 100%; }
  #panels { display: grid; grid-template-columns: 1fr 1.2fr; gap: 18px; }
  section { background: #1a1d23; border: 1px solid #2a2f38; border-radius: 10px; padding: 14px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #2a2f38; vertical-align: top; }
  th { color: #8b93a1; font-weight: 500; }
  td pre { margin: 0; max-width: 400px; max-height: 90px; overflow: auto; color: #9aa4b2; font-size: 12px; }
  .st { font-size: 12px; padding: 2px 8px; border-radius: 999px; white-space: nowrap; }
  .st-completed { background: #14532d; } .st-running { background: #1e40af; }
  .st-retrying { background: #92400e; } .st-failed { background: #7f1d1d; }
  .st-skipped, .st-cancelled { background: #374151; color: #9ca3af; }
  .review { border: 1px solid #3a4150; border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
  .review h3 { margin: 0 0 8px; font-size: 14px; }
  .review pre { background: #14161a; padding: 8px; border-radius: 6px; overflow: auto; max-height: 160px; font-size: 12px; }
  .review .buttons { margin-top: 10px; display: flex; gap: 8px; }
  #decisions div { font-size: 12px; padding: 4px 0; border-bottom: 1px solid #232833; color: #9aa4b2; }
  #decisions .yes { color: #4ade80; } #decisions .no { color: #f87171; }
  .muted { color: #8b93a1; }
</style>
</head>
<body>
<header>
  <h1>DAG Flow</h1>
  <button onclick="startRun()">&#9654; New run</button>
</header>
<main>
  <aside>
    <div class="head"><h2>Runs</h2><span class="muted" id="run-count"></span></div>
    <div id="run-list"></div>
  </aside>
  <div id="detail">
    <div id="detail-head">
      <span id="dag-name" class="muted"></span>
      <span id="run-id" class="muted"></span>
      <span id="run-chip" class="chip"></span>
      <div id="run-error"></div>
    </div>
    <div id="panels">
      <section>
        <h2>Pipeline</h2>
        <pre id="graph" class="mermaid"></pre>
      </section>
      <section>
        <h2>Nodes</h2>
        <table>
          <thead><tr><th>Node</th><th>Status</th><th>Attempts</th><th>ms</th><th>Output / Error</th></tr></thead>
          <tbody id="nodes"></tbody>
        </table>
        <h2 style="margin-top:14px">Human review</h2>
        <div id="reviews"></div>
        <h2 style="margin-top:14px">Decisions (audit)</h2>
        <div id="decisions"></div>
      </section>
    </div>
  </div>
</main>
<script>
let selected = null;
let runList = [];
let state = null;
let lastMermaid = '';

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function detail(r) {
  return r.status === 'completed' ? JSON.stringify(r.output, null, 1) : (r.error || '');
}

async function refreshRuns() {
  try { runList = (await (await fetch('/api/runs')).json()).runs; } catch (e) { return; }
  document.getElementById('run-count').textContent = runList.length;
  document.getElementById('run-list').innerHTML = runList.map(r => `
    <div class="run-item${r.id === selected ? ' sel' : ''}" onclick="select('${esc(r.id)}')">
      <span class="chip chip-${r.result}">${r.result}</span>
      <span class="rid">${esc(r.id)}</span>
    </div>`).join('');
  if (!selected && runList.length) select(runList[0].id);
}

async function refreshState() {
  if (!selected) return;
  try { state = await (await fetch('/api/state/' + encodeURIComponent(selected))).json(); } catch (e) { return; }
  document.getElementById('dag-name').textContent = state.name || '';
  document.getElementById('run-id').textContent = '#' + state.id;
  const chip = document.getElementById('run-chip');
  chip.textContent = state.running ? 'running…' : state.result;
  chip.className = 'chip chip-' + (state.running ? 'running' : state.result);
  const err = document.getElementById('run-error');
  err.textContent = state.error || '';
  err.style.display = state.error ? '' : 'none';

  document.getElementById('nodes').innerHTML = Object.entries(state.nodes || {}).map(([name, r]) => `
    <tr>
      <td>${esc(name)}</td>
      <td><span class="st st-${r.status}">${r.status}</span></td>
      <td>${r.attempts || 0}</td>
      <td>${r.duration_ms || 0}</td>
      <td><pre>${esc(detail(r))}</pre></td>
    </tr>`).join('');

  const box = document.getElementById('reviews');
  const entries = Object.entries(state.running && state.pending ? state.pending : {});
  box.innerHTML = entries.length ? '' : '<p class="muted">No review pending.</p>';
  for (const [node, payload] of entries) {
    const card = document.createElement('div');
    card.className = 'review';
    card.innerHTML = `<h3>${esc(node)}</h3>
      <pre>${esc(JSON.stringify(payload, null, 2))}</pre>
      <div class="buttons">
        <button class="ok" onclick="decide('${esc(node)}', true)">&#10003; Approve</button>
        <button class="no" onclick="decide('${esc(node)}', false)">&#10007; Reject</button>
      </div>`;
    box.appendChild(card);
  }

  const decs = document.getElementById('decisions');
  decs.innerHTML = (state.decisions || []).length
    ? state.decisions.map(d => `<div><span class="${d.approve ? 'yes' : 'no'}">${d.approve ? '✓' : '✗'} ${esc(d.node)}</span> at ${esc(d.at)}${d.reason ? ' — ' + esc(d.reason) : ''}</div>`).join('')
    : '<p class="muted">No decisions recorded.</p>';

  if (state.mermaid !== lastMermaid) {
    lastMermaid = state.mermaid;
    const graph = document.getElementById('graph');
    graph.textContent = state.mermaid || 'graph TD\\n  none[No pipeline]';
    graph.removeAttribute('data-processed');
    if (window.mermaid) { try { await mermaid.run({nodes: [graph]}); } catch (e) {} }
  }
}

function select(id) {
  selected = id;
  lastMermaid = '';
  refreshState();
  refreshRuns();
}

async function startRun() {
  const r = await (await fetch('/api/run', {method: 'POST'})).json();
  select(r.run_id);
}

async function decide(node, approve) {
  await fetch('/api/approve/' + encodeURIComponent(selected) + '/' + encodeURIComponent(node), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({approve, reason: approve ? null : 'Rejected in web UI'}),
  });
  refreshState();
}

refreshRuns();
refreshState();
setInterval(refreshRuns, 1000);
setInterval(refreshState, 1000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
