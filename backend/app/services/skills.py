"""
技能 — 符合 Agent Skills 规范 (https://agentskills.io)。

规范核心：
- 技能 = 包含 SKILL.md 的目录（frontmatter + markdown 指令体）
- 目录结构：SKILL.md + scripts/ + references/ + assets/
- agent 通过 load_skill 工具按需加载完整指令

磁盘是唯一 source of truth，读写直接操作 SKILLS_DIR。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.core.config import settings

# ---------------------------------------------------------------------------
# SkillDef — 技能定义
# ---------------------------------------------------------------------------


class SkillDef(BaseModel):
    """一个符合 Agent Skills 规范的技能定义。"""

    name: str
    description: str
    content: str = ""  # 完整 SKILL.md 内容
    files: list[str] = Field(default_factory=list)  # 目录内所有文件（相对路径）


def find_skill(name: str) -> SkillDef | None:
    """按名称从磁盘读取技能，不存在或格式错误返回 None。"""
    skill_dir = settings.SKILLS_DIR / name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        text = skill_md.read_text(encoding="utf-8")
        _, desc = parse_skill_content(text)
    except (ValueError, OSError):
        return None
    sd = SkillDef(name=name, description=desc, content=text)
    sd.files = _collect_files(skill_dir)
    return sd


def list_all_skills() -> list[SkillDef]:
    """扫描技能目录，返回所有合法技能。"""
    result = []
    if settings.SKILLS_DIR.is_dir():
        for entry in sorted(settings.SKILLS_DIR.iterdir()):
            if entry.is_dir():
                sd = find_skill(entry.name)
                if sd:
                    result.append(sd)
    return result


# ---------------------------------------------------------------------------
# SKILL.md 解析
# ---------------------------------------------------------------------------


def parse_skill_content(text: str) -> tuple[str, str]:
    """解析 SKILL.md 内容，返回 (name, description)。格式非法或 name 不是合法目录名时抛 ValueError。"""
    parts = text.split("---", 2)
    meta: dict = {}
    if len(parts) >= 3 and not parts[0].strip():
        yaml_str = parts[1]
        fixed = re.sub(
            r'^([\w-]+):\s+(.+)$',
            lambda m: f'{m.group(1)}: "{m.group(2)}"',
            yaml_str,
            flags=re.MULTILINE,
        )
        try:
            loaded = yaml.safe_load(fixed)
        except yaml.YAMLError:
            loaded = None
        if isinstance(loaded, dict):
            meta = loaded

    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()
    if not name or not desc:
        raise ValueError("SKILL.md 格式错误：需要 frontmatter（--- 包裹）且包含 name 和 description")
    if name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError(f"技能名非法: {name!r}")
    return name, desc


def _collect_files(skill_dir: Path) -> list[str]:
    """收集目录内所有文件的相对路径。"""
    return [
        str(p.relative_to(skill_dir)).replace("\\", "/")
        for p in sorted(skill_dir.rglob("*"))
        if p.is_file()
    ]


def save_skill(sd: SkillDef) -> SkillDef:
    """写入 SKILL.md、收集文件列表。"""
    skill_dir = settings.SKILLS_DIR / sd.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(sd.content, encoding="utf-8")
    sd.files = _collect_files(skill_dir)
    return sd


def delete_skill(name: str) -> None:
    """删除技能目录。"""
    skill_dir = settings.SKILLS_DIR / name
    if not skill_dir.is_dir():
        raise ValueError(f"技能 {name!r} 不存在")
    shutil.rmtree(skill_dir)
