"""Pydantic 响应模型 — /api/v1/pipelines（demo 流水线只读浏览）"""

from pydantic import BaseModel


class PipelineListItem(BaseModel):
    """列表条目：文件名 + 元信息，不含图/源码（详情接口才给）。"""

    filename: str
    name: str
    description: str = ""
    node_count: int  # 顶层节点数（loop 的 body 子节点不计）


class PipelineNodeOut(BaseModel):
    """一个节点的展示元信息（YAML spec + 注册表元数据合成）。"""

    name: str
    kind: str  # node | human | loop
    type: str | None = None          # 注册表函数键（kind=node 才有）
    type_label: str | None = None    # 注册表 label；human=人工审核，loop=循环
    type_description: str | None = None  # node=注册表描述；human=审核提示；loop=循环体摘要
    depends_on: list[str] = []
    retry: str | None = None         # 中文摘要，如 "重试 3 次，退避 0.05s×2（≤0.5s）"
    condition: str | None = None     # 条件谓词注册表键
    condition_label: str | None = None


class PipelineDetail(PipelineListItem):
    mermaid: str
    source: str  # 原始 YAML 文本（只读展示）
    nodes: list[PipelineNodeOut]


class PipelineListResponse(BaseModel):
    pipelines: list[PipelineListItem]
