"""Pydantic 响应模型 — /api/v1/skills, /api/v1/tools"""

from pydantic import BaseModel


class RegistryItemOut(BaseModel):
    """注册表中一个条目（name + description）。"""

    name: str
    description: str


class RegistryListResponse(BaseModel):
    items: list[RegistryItemOut]
