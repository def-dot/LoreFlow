"""Agent 工具/技能构建逻辑
"""

from __future__ import annotations

import logging
from typing import Any

from app.registry.tool import TOOL_REGISTRY
from app.registry.skills import SKILL_REGISTRY


logger = logging.getLogger(__name__)

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def build_tools(tools_input: list[str]) -> list[dict[str, Any]] | None:
    """构建 OpenAI 格式工具列表。['*'] → 全部，[] → 无。"""
    tool_names = list(TOOL_REGISTRY) if "*" in tools_input else tools_input
    if not tool_names:
        return None

    result: list[dict[str, Any]] = []
    for name in tool_names:
        td = TOOL_REGISTRY.get(name)
        if td is None:
            logger.warning("未知工具：%s", name)
            continue

        schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for p in td.params:
            schema["properties"][p.name] = {
                "type": _TYPE_MAP.get(p.param_type, "string"),
                "description": p.description
            }
            if p.required:
                schema["required"].append(p.name)

        result.append({
            "type": "function",
            "function": {"name": td.name, "description": td.description, "parameters": schema},
        })
    return result or None


def build_skill_prompt(skill_names: list[str]) -> str | None:
    """构建技能目录 prompt，无技能返回 None。"""
    skill_names = list(SKILL_REGISTRY) if "*" in skill_names else skill_names
    if not skill_names:
        return None
    
    resolved = []
    for name in skill_names:
        td = SKILL_REGISTRY.get(name)
        if td is None:
            logger.warning("未知技能：%s", name)
            continue
        resolved.append(td)

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
