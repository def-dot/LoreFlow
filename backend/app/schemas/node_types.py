"""NodeType schemas."""

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import SourceInfo


class NodeTypeOut(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    #: 消费方：node = 工作流节点，tool = Agent 工具
    roles: list[str] = Field(default_factory=list)
    source: SourceInfo = Field(default_factory=SourceInfo)
