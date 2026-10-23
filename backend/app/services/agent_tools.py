"""Agent 工具/技能构建逻辑
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.registry.types import TOOL_REGISTRY, FuncDef
from app.services.skills import find_skill


logger = logging.getLogger(__name__)


def build_tools(tools_input: list[str]) -> list[dict[str, Any]] | None:
    """构建 OpenAI 格式工具列表。TOOL_REGISTRY 的 key 即 LLM function name（ASCII）。
    """
    result: list[dict[str, Any]] = []
    for name in tools_input:
        td = TOOL_REGISTRY.get(name)
        if td is None:
            logger.warning("未知工具：%s", name)
            continue
        schema = td.input_schema or {"type": "object", "properties": {}}
        result.append({
            "type": "function",
            "function": {"name": name, "description": td.description, "parameters": schema},
        })
    return result or None


def build_skill_prompt(skill_names: list[str]) -> str | None:
    """构建技能目录 prompt，无技能返回 None。"""
    skills = []
    for n in skill_names:
        td = find_skill(n)
        if td is None:
            logger.warning("未知技能：%s", n)
        else:
            skills.append(td)
    if not skills:
        return None
    catalog = "\n".join(
        f"  <skill><name>{s.name}</name><description>{s.description}</description></skill>"
        for s in skills
    )
    return (
        "以下技能提供特定任务的专业指令。当任务匹配某个技能的描述时，"
        "使用 load_skill 工具加载完整指令。"
        f"\n\n<available_skills>\n{catalog}\n</available_skills>"
    )


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------


async def execute_tool_call(tc: dict[str, Any]) -> str:
    """执行单个工具调用，返回输出文本。"""
    func_def = tc.get("function", {})
    name = func_def.get("name", "")
    try:
        args = json.loads(func_def.get("arguments", "{}"))
    except (json.JSONDecodeError, TypeError):
        args = {}

    td = TOOL_REGISTRY.get(name)
    if td is None:
        return f"未知工具：{name}"

    try:
        result = await td.invoke(args)
    except Exception as exc:
        return f"工具 {name} 执行失败：{type(exc).__name__}: {exc}"

    return json.dumps(result.model_dump(), ensure_ascii=False) if hasattr(result, 'model_dump') else str(result)
