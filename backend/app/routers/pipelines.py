"""流水线 CRUD — /api/v1/pipelines"""

from fastapi import APIRouter, HTTPException, Query

from app.core.response import UnifiedResponseRoute
from app.models.pipeline import PipelineRecord
from app.schemas.pipelines import (
    PipelineDefinitionRequest,
    PipelineDetail,
)
from app.services import pipelines as pipeline_service

router = APIRouter(prefix="/pipelines", route_class=UnifiedResponseRoute, tags=["pipelines"])


@router.get("", response_model=list[PipelineRecord])
async def list_pipelines(q: str | None = Query(None, max_length=200, description="按名称/描述模糊搜索")) -> list[PipelineRecord]:
    return await pipeline_service.list_pipelines(q=q)


@router.get("/{pipeline_id}", response_model=PipelineDetail)
async def get_pipeline(pipeline_id: int) -> PipelineDetail:
    rec = await pipeline_service.get_pipeline(pipeline_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"流水线 {pipeline_id} 不存在")
    return PipelineDetail(**pipeline_service.detail_from_config(rec.definition))


@router.post("", response_model=PipelineRecord, status_code=201)
async def create_pipeline(body: PipelineDefinitionRequest) -> PipelineRecord:
    return await pipeline_service.create_pipeline(definition=body.definition)


@router.put("/{pipeline_id}", response_model=PipelineRecord)
async def update_pipeline(pipeline_id: int, body: PipelineDefinitionRequest) -> PipelineRecord:
    return await pipeline_service.update_pipeline(pipeline_id, definition=body.definition)


@router.delete("/{pipeline_id}")
async def delete_pipeline(pipeline_id: int) -> dict[str, int]:
    if await pipeline_service.delete_pipeline(pipeline_id):
        return {"deleted": pipeline_id}
    raise HTTPException(status_code=404, detail=f"流水线 {pipeline_id} 不存在")
