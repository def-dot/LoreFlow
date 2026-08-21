"""插件状态 — /api/v1/plugins（只读列表；插件变更由后台轮询自动生效）"""

from dataclasses import asdict

from fastapi import APIRouter

from app.core.response import UnifiedResponseRoute
from app.registry import plugins as plugin_loader
from app.schemas.plugins import PluginListResponse, PluginOut

router = APIRouter(prefix="/plugins", route_class=UnifiedResponseRoute, tags=["plugins"])


@router.get("", response_model=PluginListResponse)
async def list_plugins() -> PluginListResponse:
    return PluginListResponse(
        plugins=[PluginOut(**asdict(p)) for p in plugin_loader.list_plugins()]
    )
