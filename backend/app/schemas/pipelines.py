"""Pydantic 响应模型 — /api/v1/pipelines（demo 流水线只读浏览）"""

from typing import Any

from pydantic import BaseModel


class PipelineListItem(BaseModel):
    """列表条目：文件名 + 元信息，不含图/源码（详情接口才给）。"""

    filename: str
    name: str
    description: str = ""
    node_count: int  # 顶层节点数（loop 的 body 子节点不计）
    params: dict[str, Any] = {}  # YAML inputs 原始结构


class PipelineNodeOut(BaseModel):
    """一个节点的展示元信息（YAML spec + 注册表元数据合成）。"""

    name: str
    label: str | None = None
    type: str | None = None
    type_label: str | None = None
    description: str | None = None
    type_description: str | None = None
    depends_on: list[str] = []
    inputs: dict[str, Any] | None = None
    retry: str | None = None
    condition: str | dict[str, Any] | None = None


class PipelineDetail(PipelineListItem):
    mermaid: str
    source: str  # 原始 YAML 文本（只读展示）
    nodes: list[PipelineNodeOut]


class PipelineListResponse(BaseModel):
    pipelines: list[PipelineListItem]
