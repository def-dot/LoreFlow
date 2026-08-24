"""Pydantic 响应模型 — /api/v1/pipelines（demo 流水线只读浏览）"""

from typing import Any

from pydantic import BaseModel


class PipelineParamOut(BaseModel):
    """一个运行时输入参数的声明行（YAML ``params`` 声明归一化后）。"""

    name: str                    # ctx 里的键
    label: str                   # 展示名（未声明 label 时退化为键名）
    description: str | None = None  # 参数说明，YAML 未写则为空
    default: Any = None          # 默认值（has_default=False 时无意义）
    has_default: bool = False    # 是否声明了默认值（区分 default: null）
    required: bool = False       # 创建运行时必须提供（前端据此判断必填）
    multiline: bool = False      # 多行文本（前端渲染 textarea，如文章正文）


class PipelineListItem(BaseModel):
    """列表条目：文件名 + 元信息，不含图/源码（详情接口才给）。"""

    filename: str
    name: str
    description: str = ""
    node_count: int  # 顶层节点数（loop 的 body 子节点不计）
    params: list[PipelineParamOut] = []  # 参数声明行（驱动前端参数表单）


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
    review: dict[str, dict[str, str]] | None = None  # (kind=human) 审核视图原文 {key: {label}}；None=全量上下文


class PipelineDetail(PipelineListItem):
    mermaid: str
    source: str  # 原始 YAML 文本（只读展示）
    nodes: list[PipelineNodeOut]


class PipelineListResponse(BaseModel):
    pipelines: list[PipelineListItem]
