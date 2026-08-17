"""Pydantic 请求/响应模型 — /api/v1/runs 系列"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class RunListItem(BaseModel):
    id: int
    name: str
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: str
    error: Optional[str] = None


class NodeSnapshot(BaseModel):
    """一个节点的状态快照（NodeResult.to_dict 的形状）。"""

    status: str
    output: Any = None
    error: Optional[str] = None
    attempts: int = 0
    duration_ms: float = 0


class RunDetail(RunListItem):
    config_file: str
    mermaid: str
    nodes: Dict[str, NodeSnapshot]
    reviewing: List[str]


class RunListResponse(BaseModel):
    runs: List[RunListItem]


class RunCreateResponse(BaseModel):
    run_id: int


class ApproveRequest(BaseModel):
    approve: bool
    reason: Optional[str] = None


class ApproveResponse(BaseModel):
    status: str
    run_id: int
    node: str
    approve: bool
