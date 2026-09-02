"""Run 相关 API — 挂载在 /api/v1 下的 /runs 路由组"""

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query

from app.core.response import UnifiedResponseRoute
from app.engine import NodeStatus, load_dag
from app.models.run import RunStatus
from app.schemas.pipelines import PipelineDetail
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
from app.services import pipelines as pipeline_service
from app.services import runs as run_service

router = APIRouter(prefix="/runs", route_class=UnifiedResponseRoute, tags=["runs"])


@router.post("", response_model=RunCreateResponse, status_code=201)
async def create_run(body: RunCreateRequest | None = None) -> RunCreateResponse:
    run_id = await orchestrator.create_run(
        pipeline=body.pipeline if body else None,
        name=body.name if body else None,
        inputs=body.inputs if body else None,
    )
    return RunCreateResponse(run_id=run_id)


@router.get("", response_model=RunListResponse)
async def list_runs(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    status: RunStatus | None = Query(None, description="按 run 状态筛选"),
    pipeline: str | None = Query(None, max_length=200, description="按工作流名称筛选"),
) -> RunListResponse:
    rows, total = await run_service.list_runs(
        offset=offset, limit=limit, status=status, pipeline=pipeline
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
    data = record.model_dump()
    # 从 definition 实时生成 mermaid（渲染格式改进后存量 run 自动跟上）
    if record.definition:
        try:
            config = yaml.safe_load(record.definition)
            if isinstance(config, dict):
                data["mermaid"] = load_dag(config).to_mermaid()
        except Exception:
            data["mermaid"] = ""
    cfg = yaml.safe_load(record.definition) if record.definition else {}
    nodes_cfg = cfg.get("nodes") or {}
    for name, node in data.get("nodes", {}).items():
        spec = nodes_cfg.get(name)
        if isinstance(spec, dict) and isinstance(node, dict):
            node["label"] = spec.get("label") or name
            node["description"] = spec.get("description")
    return RunDetail(**data)


@router.get("/{run_id}/config", response_model=PipelineDetail)
async def get_run_config(run_id: int) -> PipelineDetail:
    """查看配置：run 创建时钉住的 definition 快照，与当前文件解耦。"""
    record = await run_service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"运行 {run_id!r} 不存在")
    config = yaml.safe_load(record.definition)
    if not isinstance(config, dict):
        raise HTTPException(status_code=500, detail="run 配置快照解析失败：顶层不是映射")
    return PipelineDetail(**pipeline_service.detail_from_config(raw=record.definition, config=config))


@router.delete("/{run_id}")
async def delete_run(run_id: int) -> dict[str, int]:
    """删除终态 run（completed/failed/cancelled），审批决策一并清理。

    运行中/待审核的记录拒绝删除（删掉会被事件回写复活成新行，
    且中断审批/恢复流），等运行结束或先处理审核。
    """
    if await run_service.delete_run(run_id):
        return {"deleted": run_id}
    record = await run_service.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"运行 {run_id} 不存在")
    raise HTTPException(status_code=400, detail=f"仅终态记录可删除（当前：{record.status.value}）")


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: int) -> dict[str, Any]:
    """取消运行中或待审核的 run：标记为 CANCELLED，后台 pipeline 会检测并退出。
    """
    try:
        await orchestrator.cancel_run(run_id)
    except ValueError as e:
        if "不存在" in str(e):
            raise HTTPException(status_code=404, detail=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    record = await run_service.get_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"运行 {run_id} 不存在")
    return {"data": record.model_dump()}


@router.post("/{run_id}/approve/{node_name}", response_model=ApproveResponse)
async def approve_node(run_id: int, node_name: str, body: ApproveRequest) -> ApproveResponse:
    record = await run_service.get_run(run_id)
    entry = (record.nodes or {}).get(node_name) if record else None
    if (
        record is None
        or record.status != RunStatus.REVIEWING
        or not isinstance(entry, dict)
        or entry.get("status") != NodeStatus.REVIEWING.value
    ):
        raise HTTPException(status_code=404, detail=f"节点 {node_name!r} 不在等待审核")

    await orchestrator.approve_and_resume(record, node_name, body.model_dump())
    return ApproveResponse(status="ok", run_id=run_id, node=node_name, approve=body.approve)
