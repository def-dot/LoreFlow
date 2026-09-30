"""技能 / 工具目录 — 向前端枚举已注册的 skills 和 tools。"""

from fastapi import APIRouter

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.registry.plugins import plugin_owner_index
from app.registry.skills import SKILL_REGISTRY, rescan_skills
from app.registry.types import TOOL_REGISTRY, roles_of, source_of
from app.schemas.common import SourceInfo
from app.schemas.registry import SkillOut, ToolOut
from app.services.llm import list_models

router = APIRouter(prefix="", route_class=UnifiedResponseRoute, tags=["registry"])


@router.get("/skills", response_model=list[SkillOut])
async def list_skills() -> list[SkillOut]:
    return [
        SkillOut(
            name=s.name,
            description=s.description,
            body=s.body,
            location=s.location,
            base_dir=s.base_dir,
            allowed_tools=s.allowed_tools,
            license=s.license,
            compatibility=s.compatibility,
        )
        for s in sorted(SKILL_REGISTRY.values(), key=lambda s: s.name)
    ]


@router.post("/skills/rescan", response_model=dict)
async def rescan() -> dict:
    """重新扫描技能目录（免重启）。"""
    count = rescan_skills(settings.SKILLS_DIR)
    return {"count": count}


@router.get("/tools", response_model=list[ToolOut])
async def list_tools() -> list[ToolOut]:
    owner = plugin_owner_index()
    out: list[ToolOut] = []
    for t in sorted(TOOL_REGISTRY.values(), key=lambda t: t.name):
        kind, src_name = source_of(t, owner.get(t.name, ""))
        out.append(
            ToolOut(
                name=t.name,
                label=t.label or t.name,
                description=t.description,
                group=t.metadata.get("group", ""),
                input_schema=t.json_input_schema(),
                output_schema=t.json_output_schema(),
                roles=roles_of(t.name),
                source=SourceInfo(kind=kind, name=src_name),
            )
        )
    return out


@router.get("/models")
async def list_available_models() -> dict[str, list[str]]:
    """返回每个 provider 的可用模型列表。"""
    return list_models()
