"""Run schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.run import RunStatus


class RunCreateRequest(BaseModel):
    pipeline_id: int
    name: str | None = None
    inputs: dict[str, Any] | None = None


class RunCreateResponse(BaseModel):
    run_id: int


class RunListSummary(BaseModel):
    running: int = 0
    active: int = 0


class RunListResponse(BaseModel):
    items: list[Any] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50
    summary: RunListSummary = Field(default_factory=RunListSummary)


class RunDetail(BaseModel):
    id: int
    name: str = ""
    pipeline_id: int = 0
    pipeline_name: str = ""
    created_at: datetime | None = None
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.PENDING
    error: str | None = None
    nodes: dict[str, Any] | None = Field(default_factory=dict)
    inputs: dict[str, Any] | None = Field(default_factory=dict)
    output: dict[str, Any] | None = Field(default_factory=dict)
    definition: str | None = None
    mermaid: str | None = None


class ApproveRequest(BaseModel):
    model_config = {"extra": "allow"}

    approve: bool


class ApproveResponse(BaseModel):
    status: str
    run_id: int
    node: str
    approve: bool
