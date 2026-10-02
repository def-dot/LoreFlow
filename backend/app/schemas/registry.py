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
    content: str = ""
    base_dir: str = ""
    files: list[str] = Field(default_factory=list)


class SkillCreateIn(BaseModel):
    """创建/更新技能请求。"""

    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    body: str = ""
    allowed_tools: str = ""