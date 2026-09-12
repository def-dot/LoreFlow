"""Registry schemas (skills / tools)."""

from pydantic import BaseModel, Field


class RegistryItemOut(BaseModel):
    name: str
    description: str = ""
    type: str = "tool"  # "tool" | "workflow"


class RegistryListResponse(BaseModel):
    items: list[RegistryItemOut] = Field(default_factory=list)
