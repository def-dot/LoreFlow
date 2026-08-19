"""Demo 流水线只读浏览 — /api/v1/pipelines（列表 + 详情）"""

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.schemas.pipelines import PipelineDetail, PipelineListItem, PipelineListResponse
from app.services import pipelines as pipeline_service

router = APIRouter(prefix="/pipelines", route_class=UnifiedResponseRoute, tags=["pipelines"])


@router.get("", response_model=PipelineListResponse)
async def list_pipelines() -> PipelineListResponse:
    return PipelineListResponse(
        pipelines=[PipelineListItem(**entry) for entry in pipeline_service.list_pipelines()]
    )


@router.get("/{filename}", response_model=PipelineDetail)
async def get_pipeline(filename: str) -> PipelineDetail:
    return PipelineDetail(**pipeline_service.get_pipeline_detail(filename))
