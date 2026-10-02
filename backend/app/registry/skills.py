"""
技能注册表 — 符合 Agent Skills 规范 (https://agentskills.io)。

规范核心：
- 技能 = 包含 SKILL.md 的目录（frontmatter + markdown 指令体）
- 目录结构：SKILL.md + scripts/ + references/ + assets/
- agent 通过 load_skill 工具按需加载完整指令

本模块提供：
- ``SKILL_REGISTRY`` 全局注册表（name → SkillDef）
- ``discover_skills()`` 扫描目录发现技能
- ``load_skill()`` 工具函数，供 agent 按需加载技能指令
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SkillDef — 技能定义
# ---------------------------------------------------------------------------


class SkillDef(BaseModel):
    """一个符合 Agent Skills 规范的技能定义。"""

    name: str
    description: str
    content: str = ""  # 完整 SKILL.md 内容
    files: list[str] = Field(default_factory=list)  # 目录内所有文件（相对路径）


SKILL_REGISTRY: dict[str, SkillDef] = {}


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
    """写入 SKILL.md、收集文件列表、注册到注册表。"""
    skill_dir = settings.SKILLS_DIR / sd.name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(sd.content, encoding="utf-8")
    sd.files = _collect_files(skill_dir)
    SKILL_REGISTRY[sd.name] = sd
    return sd


def delete_skill(name: str) -> None:
    """删除技能目录并从注册表移除。"""
    if name not in SKILL_REGISTRY:
        raise ValueError(f"技能 {name!r} 不存在")
    del SKILL_REGISTRY[name]
    shutil.rmtree(settings.SKILLS_DIR / name, ignore_errors=True)


# ---------------------------------------------------------------------------
# 技能发现
# ---------------------------------------------------------------------------

def discover_skills(*dirs: Path) -> None:
    """扫描目录树，发现所有包含 SKILL.md 的子目录并注册到 SKILL_REGISTRY。
    """
    count = 0
    for root_path in dirs:
        if not root_path.is_dir():
            continue
        for skill_def in _scan_dir(root_path):
            SKILL_REGISTRY[skill_def.name] = skill_def
            count += 1
    logger.info("发现 %d 个技能", count)


def rescan_skills(*dirs: Path) -> int:
    """清空后重新扫描，返回发现的技能数量。供 API 热刷新，免重启。"""
    SKILL_REGISTRY.clear()
    discover_skills(*dirs)
    return len(SKILL_REGISTRY)


def _scan_dir(root: Path) -> list[SkillDef]:
    """扫描目录，返回发现的 SkillDef 列表。"""
    results: list[SkillDef] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text(encoding="utf-8")
            name, desc = parse_skill_content(text)
        except Exception:
            continue
        sd = SkillDef(name=name, description=desc, content=text, files=_collect_files(entry))
        results.append(sd)
    return results
