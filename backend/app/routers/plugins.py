"""插件管理 — /api/v1/plugins"""

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.registry import plugins as plugin_loader
from app.schemas.plugins import PluginListResponse, PluginOut

router = APIRouter(prefix="/plugins", route_class=UnifiedResponseRoute, tags=["plugins"])

PLUGINS_DIR = Path(settings.PLUGINS_DIR)


@router.get("", response_model=PluginListResponse)
async def list_plugins() -> PluginListResponse:
    return PluginListResponse(
        plugins=[PluginOut(**asdict(p)) for p in plugin_loader.list_plugins()]
    )


@router.post("", response_model=PluginOut, status_code=201)
async def upload_plugin(
    file: UploadFile = File(..., description="插件文件（.py）"),
) -> PluginOut:
    filename = file.filename or ""
    if Path(filename).suffix.lower() != ".py":
        raise ValueError("仅支持 .py 插件文件")
    stem = Path(filename).stem
    if stem.startswith("_"):
        raise ValueError("文件名不能以下划线开头")
    if not stem.isidentifier():
        raise ValueError(f"文件名 {filename!r} 不是合法的 Python 模块名")

    data = await file.read()
    if not data:
        raise ValueError("上传的文件内容为空")
    if len(data) > 1024 * 1024:
        raise ValueError("插件文件不能超过 1MB")

    # 语法检查
    try:
        compile(data, filename, "exec")
    except SyntaxError as exc:
        raise ValueError(f"Python 语法错误：{exc}") from exc

    target = PLUGINS_DIR / filename
    target.write_bytes(data)

    # 等热加载轮询发现变化（最多等一轮）
    import asyncio
    await asyncio.sleep(settings.PLUGINS_POLL_SECONDS + 0.5)

    # 返回加载结果
    for p in plugin_loader.list_plugins():
        if p.filename == filename:
            return PluginOut(**asdict(p))

    # 不应走到这里，但兜底
    raise RuntimeError(f"插件 {filename} 上传成功但未被加载")
