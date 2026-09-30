"""Registry schemas (skills / tools)."""

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import SourceInfo


class RegistryItemOut(BaseModel):
    name: str
    description: str = ""


class RegistryListResponse(BaseModel):
    items: list[RegistryItemOut] = Field(default_factory=list)


class ToolOut(BaseModel):
    """工具信息。"""

    name: str
    label: str
    description: str = ""
    group: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    #: 消费方：node = 工作流节点，tool = Agent 工具
    roles: list[str] = Field(default_factory=list)
    source: SourceInfo = Field(default_factory=SourceInfo)


class SkillOut(BaseModel):
    """技能信息（agentskills.io 规范）。"""

    name: str
    description: str = ""
    body: str = ""
    location: str = ""
    base_dir: str = ""
    allowed_tools: str = ""
    license: str = ""
    compatibility: str = ""
