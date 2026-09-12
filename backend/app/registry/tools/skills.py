"""技能工具。"""

from app.registry.tool import tool
from app.registry.skills import SKILL_REGISTRY


# @tool(description="加载技能的完整指令", params={"name": "技能名称"})
# async def load_skill(name: str) -> str:
#     sd = SKILL_REGISTRY.get(name)
#     if not sd:
#         return f"技能不存在：{name}"
#     return sd.body
