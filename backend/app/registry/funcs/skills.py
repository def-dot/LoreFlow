"""技能工具。"""

from pydantic import BaseModel, Field

from app.registry.types import tool
from app.services.skills import find_skill


class LoadSkillOutput(BaseModel):
    result: str = Field(description="技能完整指令")


class LoadSkillParams(BaseModel):
    name: str = Field(description="技能名称")


@tool(description="加载技能的完整指令", label="加载技能", metadata={"group": "基础", "order": 32})
async def load_skill(params: LoadSkillParams) -> LoadSkillOutput:
    sd = find_skill(params.name)
    if not sd:
        return LoadSkillOutput(result=f"技能不存在：{params.name}")
    return LoadSkillOutput(result=sd.content)
