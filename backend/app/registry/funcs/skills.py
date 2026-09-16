"""技能工具。"""

from pydantic import BaseModel, Field

from app.registry.types import func
from app.registry.skills import SKILL_REGISTRY


class LoadSkillOutput(BaseModel):
    result: str = Field(description="技能完整指令")


@func(description="加载技能的完整指令", label="加载技能", params={"name": "技能名称"}, output_model=LoadSkillOutput)
async def load_skill(name: str) -> dict:
    sd = SKILL_REGISTRY.get(name)
    if not sd:
        return {"result": f"技能不存在：{name}"}
    return {"result": sd.body}
