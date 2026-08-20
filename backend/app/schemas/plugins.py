"""Pydantic 响应模型 — /api/v1/plugins"""

from datetime import datetime

from pydantic import BaseModel


class PluginOut(BaseModel):
    """一个已加载插件的状态条目（error 为空表示加载成功）。"""

    filename: str
    module: str
    node_names: list[str]
    loaded_at: datetime
    error: str | None


class PluginListResponse(BaseModel):
    plugins: list[PluginOut]
