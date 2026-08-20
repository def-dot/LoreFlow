"""插件管理 — /api/v1/plugins（状态列表 + 运行期重载）"""

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


@router.post("/reload", response_model=PluginListResponse)
async def reload_plugins() -> PluginListResponse:
    """重扫插件目录：新增/更新插件、清理已删除文件（坏文件跳过并记录 error）。"""
    plugin_loader.load_plugins()
    return PluginListResponse(
        plugins=[PluginOut(**asdict(p)) for p in plugin_loader.list_plugins()]
    )
