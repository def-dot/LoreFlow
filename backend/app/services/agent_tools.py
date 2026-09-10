"""Agent 工具/技能构建逻辑 — 被 node_types/agent.py 和 agent_chat.py 共用。

独立于 registry 包，避免循环导入。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def build_tools(tools_input: list[str]) -> list[dict[str, Any]] | None:
    """构建 OpenAI 格式工具列表。['*'] → 全部，[] → 无。"""
    # 延迟导入，避免模块加载时的循环依赖
    from app.registry.tool import TOOL_REGISTRY

    tool_names = list(TOOL_REGISTRY) if "*" in tools_input else tools_input
    if not tool_names:
        return None
    missing = [n for n in tool_names if n not in TOOL_REGISTRY]
    if missing:
        logger.warning("未知工具：%s", ", ".join(missing))

    result: list[dict[str, Any]] = []
    for name in tool_names:
        td = TOOL_REGISTRY.get(name)
        if td is None:
            continue
        props = {}
        for p in td.params:
            prop: dict[str, Any] = {"type": _TYPE_MAP.get(p.param_type, "string")}
            if p.description:
                prop["description"] = p.description
            props[p.name] = prop
        required = [p.name for p in td.params if p.required]
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        result.append({
            "type": "function",
            "function": {"name": td.name, "description": td.description, "parameters": schema},
        })
    return result or None


def build_skill_prompt(skill_names: list[str]) -> str | None:
    """构建技能目录 prompt，无技能返回 None。"""
    from app.registry.skills import SKILL_REGISTRY

    if not skill_names:
        return None
    if "*" in skill_names:
        skill_names = list(SKILL_REGISTRY.keys())
    resolved = [SKILL_REGISTRY[n] for n in skill_names if n in SKILL_REGISTRY]
    if len(resolved) != len(skill_names):
        missing = [n for n in skill_names if n not in SKILL_REGISTRY]
        logger.warning("未知技能：%s", ", ".join(missing))
    if not resolved:
        return None
    catalog = "\n".join(
        f"  <skill><name>{s.name}</name><description>{s.description}</description>"
        f"<location>{s.location}</location></skill>"
        for s in resolved
    )
    return (
        "以下技能提供特定任务的专业指令。当任务匹配某个技能的描述时，"
        "使用 read_file 工具读取对应 location 的 SKILL.md 加载完整指令。"
        f"\n\n<available_skills>\n{catalog}\n</available_skills>"
    )
