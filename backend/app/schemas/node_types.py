"""NodeType schemas."""

from typing import Any

from pydantic import BaseModel, Field


class NodeTypeOut(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    group: str | None = None
    input_schema: dict[str, dict[str, Any]] | None = None
    output_schema: dict[str, Any] | None = None


class NodeTypeListResponse(BaseModel):
    node_types: list[NodeTypeOut] = Field(default_factory=list)
