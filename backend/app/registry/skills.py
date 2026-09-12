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
    location: str  # SKILL.md 绝对路径
    base_dir: str  # 技能根目录（SKILL.md 所在目录）
    body: str = ""  # markdown 指令体（frontmatter 之后的内容）
    license: str = ""
    compatibility: str = ""
    allowed_tools: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


SKILL_REGISTRY: dict[str, SkillDef] = {}


# ---------------------------------------------------------------------------
# SKILL.md 解析
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_NAME_MAX_LEN = 64
_DESC_MAX_LEN = 1024


def parse_skill_md(path: Path) -> SkillDef | None:
    """解析 SKILL.md 文件，返回 SkillDef（失败返回 None）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("无法读取 %s: %s", path, exc)
        return None

    # 提取 YAML frontmatter
    if not text.startswith("---"):
        logger.warning("SKILL.md 缺少 frontmatter: %s", path)
        return None

    # 找第二个 ---
    second = text.find("---", 3)
    if second == -1:
        logger.warning("SKILL.md frontmatter 未闭合: %s", path)
        return None

    yaml_str = text[3:second]
    body = text[second + 3:].strip()

    try:
        meta = yaml.safe_load(yaml_str)
    except yaml.YAMLError:
        # 宽容处理：尝试给含冒号的值加引号
        try:
            fixed = re.sub(
                r"^(\w+):\s+(.+[^:]*)$",
                lambda m: f'{m.group(1)}: "{m.group(2)}"',
                yaml_str,
                flags=re.MULTILINE,
            )
            meta = yaml.safe_load(fixed)
        except yaml.YAMLError as exc:
            logger.warning("SKILL.md YAML 解析失败: %s — %s", path, exc)
            return None

    if not isinstance(meta, dict):
        logger.warning("SKILL.md frontmatter 非字典: %s", path)
        return None

    name = str(meta.get("name", "")).strip()
    desc = str(meta.get("description", "")).strip()

    # 宽容验证：name 不合法时警告但仍加载
    if not name:
        logger.warning("SKILL.md 缺少 name: %s — 跳过", path)
        return None
    if len(name) > _NAME_MAX_LEN:
        logger.warning("name 超过 %d 字符: %s (%s)", _NAME_MAX_LEN, name, path)
    if not _NAME_RE.match(name):
        logger.warning("name 格式不规范: %s (%s) — 仍加载", name, path)
    if not desc:
        logger.warning("SKILL.md 缺少 description: %s — 跳过", path)
        return None
    if len(desc) > _DESC_MAX_LEN:
        logger.warning("description 超过 %d 字符: %s", _DESC_MAX_LEN, path)

    # 宽容验证：name 与目录名不匹配时警告
    dir_name = path.parent.name
    if name != dir_name:
        logger.warning("name (%s) 与目录名 (%s) 不匹配: %s — 仍加载", name, dir_name, path)

    return SkillDef(
        name=name,
        description=desc,
        location=str(path),
        base_dir=str(path.parent),
        body=body,
        license=str(meta.get("license", "")),
        compatibility=str(meta.get("compatibility", "")),
        allowed_tools=str(meta.get("allowed-tools", "")),
        metadata={str(k): str(v) for k, v in (meta.get("metadata") or {}).items()},
    )


# ---------------------------------------------------------------------------
# 技能发现
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
_MAX_DEPTH = 6
_MAX_SKILLS = 2000


def discover_skills(*dirs: str | Path) -> None:
    """扫描目录树，发现所有包含 SKILL.md 的子目录并注册到 SKILL_REGISTRY。

    扫描结果覆盖同名技能（后发现的覆盖先发现的）。
    """
    count = 0
    for root in dirs:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for skill_def in _scan_dir(root_path):
            if count >= _MAX_SKILLS:
                logger.warning("技能数量达到上限 %d，停止扫描", _MAX_SKILLS)
                return
            SKILL_REGISTRY[skill_def.name] = skill_def
            count += 1
    logger.info("发现 %d 个技能", count)


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
                results.append(sd)
        else:
            _walk(entry, depth + 1, results)
