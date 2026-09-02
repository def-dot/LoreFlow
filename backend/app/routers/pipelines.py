"""流水线 CRUD — /api/v1/pipelines"""

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.schemas.pipelines import (
    PipelineCreateResponse,
    PipelineDefinitionRequest,
    PipelineDetail,
    PipelineListItem,
    PipelineListResponse,
)
from app.services import pipelines as pipeline_service

router = APIRouter(prefix="/pipelines", route_class=UnifiedResponseRoute, tags=["pipelines"])


@router.get("", response_model=PipelineListResponse)
async def list_pipelines() -> PipelineListResponse:
    return PipelineListResponse(
        pipelines=[PipelineListItem(**entry) for entry in pipeline_service.list_pipelines()]
    )


@router.get("/{name}", response_model=PipelineDetail)
async def get_pipeline(name: str) -> PipelineDetail:
    raw, config = pipeline_service.get_pipeline(name)
    return PipelineDetail(**pipeline_service.detail_from_config(raw=raw, config=config))


@router.post("", response_model=PipelineCreateResponse, status_code=201)
async def create_pipeline(body: PipelineDefinitionRequest) -> PipelineCreateResponse:
    name = pipeline_service.create_pipeline(definition=body.definition)
    return PipelineCreateResponse(name=name)


@router.put("/{name}", response_model=PipelineCreateResponse)
async def update_pipeline(name: str, body: PipelineDefinitionRequest) -> PipelineCreateResponse:
    new_name = pipeline_service.update_pipeline(name=name, definition=body.definition)
    return PipelineCreateResponse(name=new_name)


@router.delete("/{name}")
async def delete_pipeline(name: str) -> dict[str, str]:
    if pipeline_service.delete_pipeline(name):
        return {"deleted": name}
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
