"""Pipeline schemas."""

from typing import Any

from pydantic import BaseModel, Field


class NodeDetail(BaseModel):
    name: str
    label: str | None = None
    type: str | None = None
    type_label: str | None = None
    description: str | None = None
    type_description: str | None = None
    type_input_schema: dict[str, dict[str, Any]] | None = None
    type_output_schema: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list)
    inputs: Any = None
    retry: str | None = None
    condition: Any = None


class PipelineListItem(BaseModel):
    name: str
    description: str = ""
    node_count: int = 0
    params: dict[str, Any] = Field(default_factory=dict)


class PipelineListResponse(BaseModel):
    pipelines: list[PipelineListItem] = Field(default_factory=list)


class PipelineDetail(BaseModel):
    name: str
    description: str = ""
    node_count: int = 0
    mermaid: str = ""
    source: str = ""
    nodes: list[NodeDetail] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class PipelineDefinitionRequest(BaseModel):
    definition: str


class PipelineCreateResponse(BaseModel):
    name: str
