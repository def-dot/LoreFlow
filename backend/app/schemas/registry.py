"""Registry schemas (skills / tools)."""

from pydantic import BaseModel, Field


class RegistryItemOut(BaseModel):
    name: str
    description: str = ""


class RegistryListResponse(BaseModel):
    items: list[RegistryItemOut] = Field(default_factory=list)
