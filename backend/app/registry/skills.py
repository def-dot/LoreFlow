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
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SkillDef — 技能定义
# ---------------------------------------------------------------------------


@dataclass
class SkillDef:
    """一个符合 Agent Skills 规范的技能定义。"""

    name: str
    description: str
    base_dir: str  # 技能根目录（SKILL.md 所在目录）
    content: str = ""  # 完整 SKILL.md 内容
    files: list[str] = field(default_factory=list)  # 目录内所有文件（相对路径）


SKILL_REGISTRY: dict[str, SkillDef] = {}


# ---------------------------------------------------------------------------
# SKILL.md 解析
# ---------------------------------------------------------------------------


def parse_skill_md(path: Path) -> SkillDef | None:
    """解析 SKILL.md 文件，返回 SkillDef（失败返回 None）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("无法读取 %s: %s", path, exc)
        return None

    # 提取 YAML frontmatter（用 split 拆三段）
    parts = text.split("---", 2)
    if len(parts) < 3 or not parts[0].strip() == "":
        logger.warning("SKILL.md 缺少 frontmatter: %s", path)
        return None

    yaml_str, body = parts[1], parts[2].strip()

    # 预处理：给所有未加引号的值加引号，避免 YAML 解析异常
    fixed = re.sub(
        r'^([\w-]+):\s+(.+)$',
        lambda m: f'{m.group(1)}: "{m.group(2)}"',
        yaml_str,
        flags=re.MULTILINE,
    )
    try:
        meta = yaml.safe_load(fixed)
    except yaml.YAMLError as exc:
        logger.warning("SKILL.md YAML 解析失败: %s — %s", path, exc)
        return None

    if not isinstance(meta, dict):
        logger.warning("SKILL.md frontmatter 非字典: %s", path)
        return None

    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()

    # 必填字段校验
    if not name:
        logger.warning("SKILL.md 缺少 name: %s", path)
        return None
    if not desc:
        logger.warning("SKILL.md 缺少 description: %s", path)
        return None

    return SkillDef(
        name=name,
        description=desc,
        base_dir=str(path.parent),
        content=text,
    )


# ---------------------------------------------------------------------------
# 技能发现
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
_MAX_DEPTH = 6


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


def _collect_files(skill_dir: Path) -> list[str]:
    """收集技能目录内所有文件的相对路径。"""
    files: list[str] = []
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(skill_dir)).replace("\\", "/")
            if not rel.startswith(".") and not any(
                part in _SKIP_DIRS for part in rel.split("/")
            ):
                files.append(rel)
    return files


def _scan_dir(root: Path) -> list[SkillDef]:
    """递归扫描目录，返回发现的 SkillDef 列表。"""
    results: list[SkillDef] = []
    _walk(root, 0, results)
    return results


def _walk(current: Path, depth: int, results: list[SkillDef]) -> None:
    if depth > _MAX_DEPTH:
        return
    try:
        entries = sorted(current.iterdir())
    except PermissionError:
        return

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name in _SKIP_DIRS:
            continue

        skill_md = entry / "SKILL.md"
        if skill_md.is_file():
            sd = parse_skill_md(skill_md)
            if sd is not None:
                sd.files = _collect_files(entry)
                results.append(sd)
        else:
            _walk(entry, depth + 1, results)
