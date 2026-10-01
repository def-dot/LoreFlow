"""技能 / 工具目录 — 向前端枚举已注册的 skills 和 tools。"""

from fastapi import APIRouter

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.registry import REGISTRY
from app.registry.skills import SKILL_REGISTRY, rescan_skills
from app.registry.types import TOOL_REGISTRY
from app.schemas.registry import FuncOut, SkillOut
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


def _to_func_out(t) -> FuncOut:
    return FuncOut(
        name=t.name,
        label=t.label or t.name,
        description=t.description,
        metadata=t.metadata,
        input_schema=t.json_input_schema(),
        output_schema=t.json_output_schema(),
    )


@router.get("/tools", response_model=list[FuncOut])
async def list_tools() -> list[FuncOut]:
    return [_to_func_out(t) for t in sorted(TOOL_REGISTRY.values(), key=lambda t: t.name)]


@router.get("/models")
async def list_available_models() -> dict[str, list[str]]:
    """返回每个 provider 的可用模型列表。"""
    return list_models()


@router.get("/node-types", response_model=list[FuncOut])
async def list_node_types() -> list[FuncOut]:
    return [_to_func_out(t) for t in sorted(REGISTRY.values(), key=lambda t: (t.metadata.get("order", 999), t.name))]