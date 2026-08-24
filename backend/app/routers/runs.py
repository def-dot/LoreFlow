"""Run 相关 API — 挂载在 /api/v1 下的 /runs 路由组"""

from fastapi import APIRouter, HTTPException, Query

from app.core.response import UnifiedResponseRoute
from app.engine import NodeStatus
from app.models.run import RunStatus
from app.schemas.runs import (
    ApproveRequest,
    ApproveResponse,
    RunCreateRequest,
    RunCreateResponse,
    RunDetail,
    RunListResponse,
    RunListSummary,
)
from app.services import orchestrator
from app.services import reviews as review_service
from app.services import runs as run_service

router = APIRouter(prefix="/runs", route_class=UnifiedResponseRoute, tags=["runs"])


@router.post("", response_model=RunCreateResponse, status_code=201)
async def create_run(body: RunCreateRequest | None = None) -> RunCreateResponse:
    run_id = await orchestrator.create_run(
        config_file=body.config_file if body else None,
        inputs=body.inputs if body else None,
    )
    return RunCreateResponse(run_id=run_id)


@router.get("", response_model=RunListResponse)
async def list_runs(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    status: RunStatus | None = Query(None, description="按 run 状态筛选"),
    config_file: str | None = Query(None, max_length=200, description="按流水线文件名筛选"),
) -> RunListResponse:
    rows, total = await run_service.list_runs(
        offset=offset, limit=limit, status=status, config_file=config_file
    )
    counts = await run_service.run_counts()
    return RunListResponse(
        items=rows,
        total=total,
        offset=offset,
        limit=limit,
        summary=RunListSummary(**counts),
    )


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int) -> RunDetail:
    record = await run_service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"运行 {run_id!r} 不存在")
    return RunDetail(**record.model_dump())


@router.post("/{run_id}/approve/{node_name}", response_model=ApproveResponse)
async def approve_node(run_id: int, node_name: str, body: ApproveRequest) -> ApproveResponse:
    record = await run_service.get_run(run_id)
    entry = (record.nodes or {}).get(node_name) if record else None
    if record is None or not isinstance(entry, dict) or entry.get("status") != NodeStatus.REVIEWING.value:
        raise HTTPException(status_code=404, detail=f"节点 {node_name!r} 不在等待审核")
    await review_service.create_decision(run_id, node_name, {"approve": body.approve, "reason": body.reason})
    await orchestrator.resume_record(record)
    return ApproveResponse(status="ok", run_id=run_id, node=node_name, approve=body.approve)
