"""Run 相关 API — 挂载在 /api/v1 下的 /runs 路由组"""

from fastapi import APIRouter, HTTPException

from app.core.response import UnifiedResponseRoute
from app.engine import NodeStatus
from app.schemas.runs import (
    ApproveRequest,
    ApproveResponse,
    RunCreateResponse,
    RunDetail,
    RunListItem,
)
from app.services import orchestrator
from app.services import reviews as review_service
from app.services import runs as run_service

router = APIRouter(prefix="/runs", route_class=UnifiedResponseRoute, tags=["runs"])


@router.post("", response_model=RunCreateResponse, status_code=201)
async def create_run() -> RunCreateResponse:
    # 先校验配置（load_dag 构建失败抛 ValueError），通过后才落库——
    # 配置错误直接 400，不产生垃圾 run 记录
    try:
        run_id = await orchestrator.create_run()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RunCreateResponse(run_id=run_id)


@router.get("", response_model=list[RunListItem])
async def list_runs() -> list[RunListItem]:
    return await run_service.list_runs()


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: int) -> RunDetail:
    record = await run_service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"运行 {run_id!r} 不存在")
    reviewing = sorted(
        n for n, e in record.nodes.items() if isinstance(e, dict) and e.get("status") == NodeStatus.REVIEWING.value
    )
    return RunDetail(**record.model_dump(), reviewing=reviewing)


@router.post("/{run_id}/approve/{node_name}", response_model=ApproveResponse)
async def approve_node(run_id: int, node_name: str, body: ApproveRequest) -> ApproveResponse:
    record = await run_service.get_run(run_id)
    entry = (record.nodes or {}).get(node_name) if record else None
    if not isinstance(entry, dict) or entry.get("status") != NodeStatus.REVIEWING.value:
        raise HTTPException(status_code=404, detail=f"节点 {node_name!r} 不在等待审核")
    await review_service.create_decision(run_id, node_name, {"approve": body.approve, "reason": body.reason})
    return ApproveResponse(status="ok", run_id=run_id, node=node_name, approve=body.approve)
