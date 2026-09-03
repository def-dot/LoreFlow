"""Pydantic 响应模型 — /api/v1/node-types"""

from typing import Any

from pydantic import BaseModel


class NodeTypeOut(BaseModel):
    """一个节点类型的目录条目（不含实现，仅供枚举/展示）。"""

    name: str
    label: str
    description: str
    group: str | None = None
    input_schema: dict[str, dict[str, Any]] | None = None
    output_schema: dict[str, Any] | None = None


class NodeTypeListResponse(BaseModel):
    node_types: list[NodeTypeOut]
