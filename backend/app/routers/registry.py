"""技能 / 工具目录 — 向前端枚举已注册的 skills 和 tools。"""

import shutil
import zipfile
from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from app.core.config import settings
from app.core.response import UnifiedResponseRoute
from app.registry import REGISTRY
from app.registry.skills import SKILL_REGISTRY, rescan_skills
from app.registry.types import TOOL_REGISTRY
from app.schemas.registry import FuncOut, SkillCreateIn, SkillOut
from app.services.llm import list_models

router = APIRouter(prefix="", route_class=UnifiedResponseRoute, tags=["registry"])


@router.get("/skills", response_model=list[SkillOut])
async def list_skills() -> list[SkillOut]:
    return [
        SkillOut(
            name=s.name,
            description=s.description,
            content=s.content,
            base_dir=s.base_dir,
            files=s.files,
        )
        for s in sorted(SKILL_REGISTRY.values(), key=lambda s: s.name)
    ]


@router.post("/skills/rescan", response_model=dict)
async def rescan() -> dict:
    """重新扫描技能目录（免重启）。"""
    count = rescan_skills(settings.SKILLS_DIR)
    return {"count": count}


def _write_skill_md(dir: Path, data: SkillCreateIn) -> None:
    """写入 SKILL.md 文件。"""
    dir.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append(f"name: {data.name}")
    if data.description:
        lines.append(f"description: {data.description}")
    if data.allowed_tools:
        lines.append(f"allowed-tools: {data.allowed_tools}")
    lines.append("---")
    if data.body:
        lines.append("")
        lines.append(data.body)
    (dir / "SKILL.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.post("/skills", response_model=SkillOut, status_code=201)
async def create_skill(body: SkillCreateIn) -> SkillOut:
    skill_dir = settings.SKILLS_DIR / body.name
    if (skill_dir / "SKILL.md").exists():
        raise ValueError(f"技能 {body.name!r} 已存在")
    _write_skill_md(skill_dir, body)
    rescan_skills(settings.SKILLS_DIR)
    s = SKILL_REGISTRY[body.name]
    return SkillOut(name=s.name, description=s.description, content=s.content,
                    base_dir=s.base_dir, files=s.files)


@router.put("/skills/{name}", response_model=SkillOut)
async def update_skill(name: str, body: SkillCreateIn) -> SkillOut:
    skill_dir = settings.SKILLS_DIR / name
    if not (skill_dir / "SKILL.md").exists():
        raise ValueError(f"技能 {name!r} 不存在")
    # 改名：重命名目录
    if body.name != name:
        new_dir = settings.SKILLS_DIR / body.name
        if new_dir.exists():
            raise ValueError(f"技能 {body.name!r} 已存在")
        skill_dir.rename(new_dir)
        skill_dir = new_dir
    _write_skill_md(skill_dir, body)
    rescan_skills(settings.SKILLS_DIR)
    s = SKILL_REGISTRY[body.name]
    return SkillOut(name=s.name, description=s.description, content=s.content,
                    base_dir=s.base_dir, files=s.files)


@router.delete("/skills/{name}")
async def delete_skill(name: str) -> dict:
    skill_dir = settings.SKILLS_DIR / name
    if not (skill_dir / "SKILL.md").exists():
        raise ValueError(f"技能 {name!r} 不存在")
    shutil.rmtree(skill_dir)
    rescan_skills(settings.SKILLS_DIR)
    return {"detail": f"技能 {name} 已删除"}


@router.get("/skills/{name}/file")
async def read_skill_file(name: str, path: str) -> str:
    """读取技能目录内的文件内容。"""
    sd = SKILL_REGISTRY.get(name)
    if not sd:
        raise ValueError(f"技能 {name!r} 不存在")
    base = Path(sd.base_dir)
    target = (base / path).resolve()
    # 安全检查：不允许跳出技能目录
    if not str(target).startswith(str(base.resolve())):
        raise ValueError("非法路径")
    if not target.is_file():
        raise ValueError(f"文件不存在: {path}")
    # 限制大小 100KB
    if target.stat().st_size > 100 * 1024:
        raise ValueError("文件过大（超过 100KB）")
    return target.read_text(encoding="utf-8")


@router.post("/skills/upload")
async def upload_skill_zip(file: UploadFile = File(..., description="技能包 zip 文件")) -> dict:
    if not file.filename or not file.filename.endswith(".zip"):
        raise ValueError("仅支持 .zip 文件")
    data = await file.read()
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("文件不能超过 10MB")

    skills_dir = settings.SKILLS_DIR
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for info in zf.infolist():
            # 安全检查：不允许 ..
            if ".." in info.filename:
                raise ValueError(f"非法路径: {info.filename}")
        zf.extractall(skills_dir)

    count = rescan_skills(skills_dir)
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