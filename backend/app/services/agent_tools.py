"""Agent 工具/技能构建逻辑
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.registry.types import TOOL_REGISTRY, FuncDef
from app.registry.skills import SKILL_REGISTRY


logger = logging.getLogger(__name__)


async def build_tools(tools_input: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """构建 OpenAI 格式工具列表 + Pipeline 目录 prompt。

    返回 ``(tools, pipeline_prompt)`` — tools 为 None 表示无工具。
    ``"*"`` 匹配全部工具（含 Pipeline）；``"pipeline"`` 匹配全部 Pipeline 工具。
    """
    select_all = "*" in tools_input
    select_all_pipeline = select_all or "pipeline" in tools_input

    # 普通工具名（去掉 "*"/"pipeline" 通配符）
    normal_names = [t for t in tools_input if t not in ("*", "pipeline")]

    result: list[dict[str, Any]] = []
    pipeline_refs: list[str] = []

    for name, td in TOOL_REGISTRY.items():
        is_pipeline = td.metadata.get("group") == "pipeline"

        if is_pipeline:
            if select_all_pipeline:
                pipeline_refs.append(name)
                result.append(_tooldef_to_openai(td))
            elif name in normal_names:
                normal_names.remove(name)
                pipeline_refs.append(name)
                result.append(_tooldef_to_openai(td))
        else:
            if select_all or name in normal_names:
                if name in normal_names:
                    normal_names.remove(name)
                result.append(_tooldef_to_openai(td))

    for name in normal_names:
        logger.warning("未知工具：%s", name)

    # 构建 pipeline 目录 prompt
    pipeline_prompt = await build_pipeline_prompt(select_all_pipeline, pipeline_refs)

    return result or None, pipeline_prompt


def _tooldef_to_openai(td: FuncDef) -> dict[str, Any]:
    """FuncDef → OpenAI function calling 格式。"""
    schema = td.json_input_schema() or {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {"name": td.name, "description": td.description, "parameters": schema},
    }


async def build_pipeline_prompt(select_all: bool, refs: list[str]) -> str | None:
    """构建可用 Pipeline 的结构化目录，注入 Agent system prompt。"""
    pipelines: list[dict[str, Any]] = []
    for name in refs:
        td = TOOL_REGISTRY.get(name)
        if td is None:
            continue

        # 从 JSON Schema 提取参数信息
        schema = td.json_input_schema() or {}
        props = schema.get("properties", {})
        required_set = set(schema.get("required", []))
        params_info: list[str] = []
        for pname, pschema in props.items():
            ptype = pschema.get("type", "string")
            pdesc = pschema.get("description", pname)
            req = "必填" if pname in required_set else "可选"
            enum_info = ""
            if "enum" in pschema:
                enum_info = f"，可选值：{pschema['enum']}"
            params_info.append(f"    - {pname} ({ptype}, {req})：{pdesc}{enum_info}")

        params_block = "\n".join(params_info) if params_info else "    （无参数）"
        pipelines.append(f"  <workflow name=\"{name}\">\n"
                         f"    <description>{td.description}</description>\n"
                         f"    <params>\n{params_block}\n    </params>\n"
                         f"  </workflow>")

    if not pipelines:
        return None

    catalog = "\n".join(pipelines)
    return (
        "以下工作流可通过 run_pipeline 工具执行。"
        "当用户任务匹配某个工作流的描述时，调用该工具并将所需参数传入 inputs 字段。\n\n"
        f"<workflows>\n{catalog}\n</workflows>"
    )


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
            output = await td.invoke(args)
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
