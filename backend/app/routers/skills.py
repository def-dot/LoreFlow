"""技能 CRUD。"""

import zipfile
from io import BytesIO

from fastapi import APIRouter, File, UploadFile

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.registry.skills import SKILL_REGISTRY, SkillDef, delete_skill, parse_skill_content, save_skill
from app.schemas.registry import SkillCreateIn

router = APIRouter(prefix="/skills", route_class=UnifiedResponseRoute, tags=["skills"])


@router.get("", response_model=list[SkillDef])
async def list_skills() -> list[SkillDef]:
    return sorted(SKILL_REGISTRY.values(), key=lambda s: s.name)


@router.post("", response_model=SkillDef, status_code=201)
async def create_skill(body: SkillCreateIn) -> SkillDef:
    name, desc = parse_skill_content(body.content)
    if name in SKILL_REGISTRY:
        raise ValueError(f"技能 {name!r} 已存在")
    sd = SkillDef(name=name, description=desc, content=body.content)
    return save_skill(sd)


@router.put("/{name}", response_model=SkillDef)
async def update_skill(name: str, body: SkillCreateIn) -> SkillDef:
    sd = SKILL_REGISTRY.get(name)
    if not sd:
        raise ValueError(f"技能 {name!r} 不存在")
    new_name, description = parse_skill_content(body.content)
    if new_name != name:
        if new_name in SKILL_REGISTRY:
            raise ValueError(f"技能 {new_name!r} 已存在")
        SKILL_REGISTRY.pop(name)
        (settings.SKILLS_DIR / name).rename(settings.SKILLS_DIR / new_name)
    sd.name = new_name
    sd.description = description
    sd.content = body.content
    return save_skill(sd)


@router.delete("/{name}")
async def remove_skill(name: str) -> dict:
    delete_skill(name)
    return {"detail": f"技能 {name} 已删除"}


@router.get("/{name}/file")
async def read_skill_file(name: str, path: str) -> str:
    """读取技能目录内的文件内容。"""
    if name not in SKILL_REGISTRY:
        raise ValueError(f"技能 {name!r} 不存在")
    base = (settings.SKILLS_DIR / name).resolve()
    target = (base / path).resolve()
    if not target.is_relative_to(base):  # 不允许跳出技能目录
        raise ValueError("非法路径")
    if not target.is_file():
        raise ValueError(f"文件不存在: {path}")
    if target.stat().st_size > 100 * 1024:
        raise ValueError("文件过大（超过 100KB）")
    return target.read_text(encoding="utf-8")


async def _parse_skill_zip(file: UploadFile) -> tuple[zipfile.ZipFile, SkillDef]:
    """读取 zip、校验、解析 SKILL.md，返回 (ZipFile, SkillDef)。"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise ValueError("仅支持 .zip 文件")
    data = await file.read()
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("文件不能超过 10MB")
    zf = zipfile.ZipFile(BytesIO(data))
    try:
        for info in zf.infolist():
            if ".." in info.filename:
                raise ValueError(f"非法路径: {info.filename}")
        if "SKILL.md" not in zf.namelist():
            raise ValueError("zip 包缺少 SKILL.md")
        text = zf.read("SKILL.md").decode("utf-8")
        name, desc = parse_skill_content(text)
    except Exception:
        zf.close()
        raise
    return zf, SkillDef(name=name, description=desc, content=text)


@router.post("/upload", response_model=SkillDef, status_code=201)
async def upload_skill_zip(file: UploadFile = File(..., description="技能包 zip 文件")) -> SkillDef:
    zf, sd = await _parse_skill_zip(file)
    with zf:
        if sd.name in SKILL_REGISTRY:
            raise ValueError(f"技能 {sd.name!r} 已存在")
        zf.extractall(settings.SKILLS_DIR / sd.name)
    return save_skill(sd)


@router.put("/{name}/upload", response_model=SkillDef)
async def update_skill_zip(name: str, file: UploadFile = File(..., description="技能包 zip 文件")) -> SkillDef:
    if name not in SKILL_REGISTRY:
        raise ValueError(f"技能 {name!r} 不存在")
    zf, sd = await _parse_skill_zip(file)
    with zf:
        if sd.name != name:
            if sd.name in SKILL_REGISTRY:
                raise ValueError(f"技能 {sd.name!r} 已存在")
        delete_skill(name)
        zf.extractall(settings.SKILLS_DIR / sd.name)
    return save_skill(sd)
