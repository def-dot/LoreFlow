"""Pydantic 请求/响应模型 — /api/v1/runs 系列"""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


class RunListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: str | None = None
    finished_at: str | None = None
    status: str
    error: str | None = None
    config_file: str = ""  # 流水线文件名：列表筛选/展示用

    @field_serializer("created_at", "finished_at")
    def _format_dt(self, value: str | None) -> str | None:
        """存储层是 ISO（带 T），响应层换成空格分隔的友好格式。"""
        return value.replace("T", " ", 1) if value else value


class RunListSummary(BaseModel):
    """全局执行计数（不受列表筛选影响）：前端轮询启停（running）与
    页面状态灯（active，含待审核）据此判断，避免筛过的视图停摆轮询。"""

    running: int = 0
    active: int = 0


class RunListResponse(BaseModel):
    """GET /runs 分页响应：本页条目 + 筛选后总数 + 全局执行计数。"""

    items: list[RunListItem]
    total: int
    offset: int
    limit: int
    summary: RunListSummary = RunListSummary()


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
    inputs: dict[str, Any] = {}  # 创建时的运行时输入快照

    @field_validator("inputs", mode="before")
    @classmethod
    def _inputs_none_to_empty(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        """迁移前创建的旧记录 inputs 列为 NULL，响应层归一为空对象。"""
        return value or {}


class RunCreateRequest(BaseModel):
    """POST /runs 请求体：可选 config_file（缺省用人工审核演示流水线）与
    inputs（运行时输入，进入共享上下文；与 YAML 顶层 inputs 合并，同名键
    运行时优先；键不能与节点名冲突）。"""

    config_file: str | None = None
    inputs: dict[str, Any] | None = None


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
