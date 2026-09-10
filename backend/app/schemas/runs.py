"""Run schemas."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    pipeline: str | None = None
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
    pipeline: str = ""
    created_at: datetime | None = None
    finished_at: datetime | None = None
    status: str = "pending"
    error: str | None = None
    nodes: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
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
