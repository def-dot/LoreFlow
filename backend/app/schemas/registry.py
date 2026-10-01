"""Registry schemas (skills / tools / node types)."""

from typing import Any

from pydantic import BaseModel, Field


class FuncOut(BaseModel):
    """工具 / 节点类型信息。"""

    name: str
    label: str = ""
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


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