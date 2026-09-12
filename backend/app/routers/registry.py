"""技能 / 工具目录 — 向前端枚举已注册的 skills 和 tools。"""

from typing import Any

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.registry.skills import SKILL_REGISTRY
from app.registry.tool import TOOL_REGISTRY
from app.schemas.registry import RegistryItemOut, RegistryListResponse
from app.services.llm import list_models
from app.services import pipelines as pipeline_service

router = APIRouter(prefix="", route_class=UnifiedResponseRoute, tags=["registry"])


@router.get("/skills", response_model=RegistryListResponse)
async def list_skills() -> RegistryListResponse:
    return RegistryListResponse(
        items=[
            RegistryItemOut(name=s.name, description=s.description)
            for s in SKILL_REGISTRY.values()
        ]
    )


@router.get("/tools", response_model=RegistryListResponse)
async def list_tools() -> RegistryListResponse:
    items = [
        RegistryItemOut(name=t.name, description=t.description)
        for t in TOOL_REGISTRY.values()
    ]
    # Pipeline 工作流
    for entry in pipeline_service.list_pipelines():
        items.append(RegistryItemOut(
            name=entry["name"],
            description=entry.get("description") or "",
            type="workflow",
        ))
    return RegistryListResponse(items=items)


@router.get("/models")
async def list_available_models() -> dict[str, list[str]]:
    """返回每个 provider 的可用模型列表。"""
    return list_models()
