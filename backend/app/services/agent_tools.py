"""Agent 工具/技能构建逻辑
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.registry.tool import TOOL_REGISTRY, ToolDef, ParamDef
from app.registry.skills import SKILL_REGISTRY


logger = logging.getLogger(__name__)

# Pipeline 轮询等待参数
_PIPELINE_POLL_INTERVAL = 2.0   # 秒
_PIPELINE_POLL_TIMEOUT = 300.0  # 5 分钟


def build_tools(tools_input: list[str]) -> list[dict[str, Any]] | None:
    """构建 OpenAI 格式工具列表。['*'] → 全部（含 MCP），[] → 无。

    Pipeline 名称不在 TOOL_REGISTRY 中时，动态包装为工具。
    '*' 通配符不包含 pipeline（需显式选择）。
    """
    select_all = "*" in tools_input
    tool_names = list(TOOL_REGISTRY) if select_all else tools_input
    if not tool_names and not select_all:
        return None

    result: list[dict[str, Any]] = []

    for name in tool_names:
        td = TOOL_REGISTRY.get(name)
        if td is not None:
            result.append(_tooldef_to_openai(td))
            continue
        # 尝试 pipeline
        ptd = _resolve_pipeline_tool(name)
        if ptd is not None:
            result.append(_tooldef_to_openai(ptd))
        else:
            logger.warning("未知工具：%s", name)

    return result or None


# ---------------------------------------------------------------------------
# Pipeline → ToolDef 动态包装
# ---------------------------------------------------------------------------


def _resolve_pipeline_tool(name: str) -> ToolDef | None:
    """如果 name 匹配一个 pipeline，返回包装后的 ToolDef；否则 None。"""
    from app.services import pipelines as pipeline_service

    try:
        _, config = pipeline_service.get_pipeline(name)
    except Exception:
        return None

    description = config.get("description") or f"执行工作流 {name}"
    description = f"[workflow] {description}"
    params_cfg: dict[str, Any] = config.get("inputs") or {}

    param_defs: list[ParamDef] = []
    for pname, spec in params_cfg.items():
        if not isinstance(spec, dict):
            continue
        param_defs.append(ParamDef(
            name=pname,
            param_type=spec.get("type", "string"),
            description=spec.get("description") or "",
            required=spec.get("required", True),
        ))

    async def _pipeline_wrapper(**kwargs: Any) -> str:
        return await _execute_pipeline(name, kwargs)

    _pipeline_wrapper.__name__ = name
    return ToolDef(name=name, func=_pipeline_wrapper, description=description, params=param_defs)


async def _execute_pipeline(pipeline_name: str, inputs: dict[str, Any]) -> str:
    """执行一个 pipeline 并等待结果，返回摘要文本。"""
    from app.models.run import RunStatus
    from app.services import runs
    from app.services.orchestrator import create_run

    run_id = await create_run(pipeline=pipeline_name, inputs=inputs)
    logger.info("[pipeline-tool] started run %d for %s", run_id, pipeline_name)

    elapsed = 0.0
    while elapsed < _PIPELINE_POLL_TIMEOUT:
        await asyncio.sleep(_PIPELINE_POLL_INTERVAL)
        elapsed += _PIPELINE_POLL_INTERVAL
        record = await runs.get_run(run_id)
        if record is None:
            return json.dumps({"run_id": run_id, "error": "运行记录不存在"}, ensure_ascii=False)
        if record.status in runs.TERMINAL_STATUSES:
            if record.status == RunStatus.COMPLETED:
                output = record.output if record.output else "(无输出)"
                if isinstance(output, dict):
                    output = json.dumps(output, ensure_ascii=False, default=str)
                return str(output)
            else:
                return json.dumps({
                    "run_id": run_id,
                    "status": record.status.value,
                    "error": record.error or "未知错误",
                }, ensure_ascii=False)

    return json.dumps({"run_id": run_id, "error": "执行超时（5分钟）"}, ensure_ascii=False)


def _tooldef_to_openai(td: ToolDef) -> dict[str, Any]:
    """ToolDef → OpenAI function calling 格式。"""
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for p in td.params:
        schema["properties"][p.name] = {
            "type": p.param_type,
            "description": p.description,
        }
        if p.required:
            schema["required"].append(p.name)
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
        f"  <skill><name>{s.name}</name><description>{s.description}</description>"
        f"<location>{s.location}</location></skill>"
        for s in resolved
    )
    return (
        "以下技能提供特定任务的专业指令。当任务匹配某个技能的描述时，"
        "使用 filesystem__read_file 工具加载完整指令。"
        f"\n\n<available_skills>\n{catalog}\n</available_skills>"
    )
