"""NodeType schemas."""

from typing import Any

from pydantic import BaseModel, Field


class NodeTypeOut(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
