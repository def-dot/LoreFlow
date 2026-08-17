"""Run 相关 API — 挂载在 /api/v1 下的 /runs 路由组"""

from fastapi import APIRouter, HTTPException

from app.core import database
from app.core.response import UnifiedResponseRoute
from app.engine import NodeStatus
from app.schemas.runs import (
    ApproveRequest,
    ApproveResponse,
    RunCreateResponse,
    RunDetail,
    RunListItem,
    RunListResponse,
)
from app.services import runs as run_service

router = APIRouter(prefix="/runs", route_class=UnifiedResponseRoute, tags=["runs"])


@router.post("", response_model=RunCreateResponse, status_code=201)
async def create_run() -> RunCreateResponse:
    # 先校验配置（load_dag 构建失败抛 ValueError），通过后才落库——
    # 配置错误直接 400，不产生垃圾 run 记录
    try:
        run_id = await run_service.create_run()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunCreateResponse(run_id=run_id)


@router.get("", response_model=RunListResponse)
async def list_runs() -> RunListResponse:
    # 数据库是唯一真相（活跃 run 的进度也已按事件落库）
    records = (await database.load()).values()
    items = sorted(records, key=lambda s: s.created_at or "", reverse=True)
    return RunListResponse(
        runs=[
            RunListItem(
                id=s.id or 0,
                name=s.name,
                created_at=s.created_at,
                finished_at=s.finished_at,
                status=s.status,
                error=s.error,
            )
            for s in items
        ]
    )


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int) -> RunDetail:
    record = await database.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Unknown run {run_id!r}")
    reviewing = sorted(
        n for n, e in record.nodes.items()
        if isinstance(e, dict) and e.get("status") == NodeStatus.REVIEWING.value
    )
    return RunDetail(**record.model_dump(), reviewing=reviewing)


@router.post("/{run_id}/approve/{node_name}", response_model=ApproveResponse)
async def approve_node(run_id: int, node_name: str, body: ApproveRequest) -> ApproveResponse:
    record = await database.get(run_id)
    entry = (record.nodes or {}).get(node_name) if record else None
    if not isinstance(entry, dict) or entry.get("status") != NodeStatus.REVIEWING.value:
        raise HTTPException(
            status_code=404, detail=f"Node {node_name!r} is not awaiting review"
        )
    await database.save_decision(
        run_id, node_name, {"approve": body.approve, "reason": body.reason}
    )
    return ApproveResponse(status="ok", run_id=run_id, node=node_name, approve=body.approve)
