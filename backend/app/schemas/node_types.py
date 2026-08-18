"""Pydantic 响应模型 — /api/v1/node-types"""

from pydantic import BaseModel


class NodeTypeOut(BaseModel):
    """一个节点类型的目录条目（不含实现，仅供枚举/展示）。"""

    name: str
    kind: str  # function | condition
    label: str
    description: str


class NodeTypeListResponse(BaseModel):
    node_types: list[NodeTypeOut]
