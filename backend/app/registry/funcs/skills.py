"""技能工具。"""

from pydantic import BaseModel, Field

from app.registry.types import func
from app.registry.skills import SKILL_REGISTRY


class LoadSkillOutput(BaseModel):
    result: str = Field(description="技能完整指令")


class LoadSkillParams(BaseModel):
    name: str = Field(description="技能名称")


@func(node=False, description="加载技能的完整指令", label="加载技能")
async def load_skill(params: LoadSkillParams) -> LoadSkillOutput:
    sd = SKILL_REGISTRY.get(params.name)
    if not sd:
        return LoadSkillOutput(result=f"技能不存在：{params.name}")
    return LoadSkillOutput(result=sd.body)
