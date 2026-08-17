"""Pydantic 请求/响应模型 — /api/v1/runs 系列"""

from typing import Any

from pydantic import BaseModel


class RunListItem(BaseModel):
    id: int
    name: str
    created_at: str | None = None
    finished_at: str | None = None
    status: str
    error: str | None = None


class NodeSnapshot(BaseModel):
    """一个节点的状态快照（NodeResult.to_dict 的形状）。"""

    status: str
    output: Any = None
    error: str | None = None
    attempts: int = 0
    duration_ms: float = 0


class RunDetail(RunListItem):
    config_file: str
    mermaid: str
    nodes: dict[str, NodeSnapshot]
    reviewing: list[str]


class RunListResponse(BaseModel):
    runs: list[RunListItem]


class RunCreateResponse(BaseModel):
    run_id: int


class ApproveRequest(BaseModel):
    approve: bool
    reason: str | None = None


class ApproveResponse(BaseModel):
    status: str
    run_id: int
    node: str
    approve: bool
