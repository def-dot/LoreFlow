"""Pydantic 请求/响应模型 — /api/v1/runs 系列"""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from app.models.run import RunStatus


class RunListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: str | None = None
    finished_at: str | None = None
    status: RunStatus
    error: str | None = None
    pipeline: str = ""  # 工作流名称（YAML 的 name 字段）

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


class RunDetail(RunListItem):
    mermaid: str
    definition: str | None = None  # YAML 定义快照
    nodes: dict[str, Any]
    inputs: dict[str, Any] = {}  # 创建时的运行时输入快照

    @field_validator("inputs", mode="before")
    @classmethod
    def _inputs_none_to_empty(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        """迁移前创建的旧记录 inputs 列为 NULL，响应层归一为空对象。"""
        return value or {}


class RunCreateRequest(BaseModel):
    """POST /runs 请求体：可选 pipeline（缺省用人工审核演示流水线）与
    inputs（运行时输入，必须与 YAML inputs 声明的键一致；多传或错传会被拒绝）。"""

    pipeline: str | None = None
    name: str | None = None  # 任务名称，不传则自动生成
    inputs: dict[str, Any] | None = None


class RunCreateResponse(BaseModel):
    run_id: int


class ApproveRequest(BaseModel):
    """审批决策：通过/拒绝 + 可选拒绝原因 + 审核返回的卡片字段。

    卡片字段（``_review`` 声明的键）平铺在 body 顶层，与协议键
    approve/reason 同级（``$节点.decision.键`` 直接可取）；文本字段
    可就地修改后原样送回，未改即原值，仅通过时生效——改动可由
    payload 与决策字段对比得出，随决策入库留档。
    """

    model_config = ConfigDict(extra="allow")

    approve: bool
    reason: str | None = None


class ApproveResponse(BaseModel):
    status: str
    run_id: int
    node: str
    approve: bool
