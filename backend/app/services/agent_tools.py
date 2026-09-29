"""Agent 工具/技能构建逻辑
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.registry.types import TOOL_REGISTRY, FuncDef
from app.registry.skills import SKILL_REGISTRY


logger = logging.getLogger(__name__)


async def build_tools(tools_input: list[str]) -> list[dict[str, Any]] | None:
    """构建 OpenAI 格式工具列表。['*'] → 全部，[] → 无。"""
    select_all = "*" in tools_input
    tool_names = list(TOOL_REGISTRY) if select_all else tools_input
    if not tool_names and not select_all:
        return None

    result: list[dict[str, Any]] = []
    for name in tool_names:
        td = TOOL_REGISTRY.get(name)
        if td is not None:
            result.append(_tooldef_to_openai(td))
        else:
            logger.warning("未知工具：%s", name)

    return result or None


def _tooldef_to_openai(td: FuncDef) -> dict[str, Any]:
    """FuncDef → OpenAI function calling 格式。"""
    schema = td.input_schema.model_json_schema() if td.input_schema else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {"name": td.name, "description": td.description, "parameters": schema},
    }


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
        f"  <skill><name>{s.name}</name><description>{s.description}</description></skill>"
        for s in resolved
    )
    return (
        "以下技能提供特定任务的专业指令。当任务匹配某个技能的描述时，"
        "使用 load_skill 工具加载完整指令。"
        f"\n\n<available_skills>\n{catalog}\n</available_skills>"
    )


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------


async def execute_tool_call(tc: dict[str, Any]) -> dict[str, Any]:
    """执行单个工具调用，返回结果。"""
    func_def = tc.get("function", {})
    name = func_def.get("name", "")
    try:
        args = json.loads(func_def.get("arguments", "{}"))
    except (json.JSONDecodeError, TypeError):
        args = {}

    td = TOOL_REGISTRY.get(name)
    status = "success"
    if td is not None:
        try:
            output = await td.func(td.input_schema(**args))
        except Exception as exc:
            output = f"工具 {name} 执行失败：{type(exc).__name__}: {exc}"
            status = "error"
    else:
        output = f"未知工具：{name}"
        status = "error"

    return {
        "tool_call_id": tc.get("id", ""),
        "tool_name": name,
        "arguments": args,
        "output": json.dumps(output.model_dump(), ensure_ascii=False) if hasattr(output, 'model_dump') else str(output),
        "status": status,
    }
