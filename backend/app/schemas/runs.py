"""Pydantic 请求/响应模型 — /api/v1/runs 系列"""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer


class RunListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: str | None = None
    finished_at: str | None = None
    status: str
    error: str | None = None

    @field_serializer("created_at", "finished_at")
    def _format_dt(self, value: str | None) -> str | None:
        """存储层是 ISO（带 T），响应层换成空格分隔的友好格式。"""
        return value.replace("T", " ", 1) if value else value


class RunListResponse(BaseModel):
    """GET /runs 分页响应：本页条目 + 全局总数。"""

    items: list[RunListItem]
    total: int
    offset: int
    limit: int


class NodeSnapshot(BaseModel):
    """一个节点的状态快照（NodeResult.to_dict 的形状，人工审核节点附 payload）。"""

    status: str
    output: Any = None
    error: str | None = None
    attempts: int = 0
    duration_ms: float = 0
    payload: Any = None


class RunDetail(RunListItem):
    config_file: str
    mermaid: str
    nodes: dict[str, NodeSnapshot]


class RunCreateRequest(BaseModel):
    """POST /runs 请求体：可选 config_file（缺省用人工审核演示流水线）。"""

    config_file: str | None = None


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
