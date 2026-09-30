"""Plugin schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class PluginOut(BaseModel):
    filename: str
    module: str
    node_names: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    loaded_at: datetime
    error: str | None = None


class PluginListResponse(BaseModel):
    plugins: list[PluginOut] = Field(default_factory=list)
