"""
agent 节点 — LLM + 工具自动循环，直到无需工具或达到最大迭代次数。
"""

from __future__ import annotations

import logging
from typing import Any

from app.registry.node_type import NodeGroup, node_type
from app.services.llm import llm_chat_call
from app.registry.skills import SKILL_REGISTRY
from app.registry.tool import TOOL_REGISTRY, execute_tool_calls

logger = logging.getLogger(__name__)

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _build_tools(tools_input: list[str]) -> list[dict[str, Any]] | None:
    """构建工具列表。['*'] → 全部，[] → 无。"""
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
            prop = {"type": _TYPE_MAP.get(p.param_type, "string")}
            if p.description:
                prop["description"] = p.description
            props[p.name] = prop
        required = [p.name for p in td.params if p.required]
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        result.append({"type": "function", "function": {"name": td.name, "description": td.description, "parameters": schema}})
    return result or None


def _build_skill_prompt(skill_names: list[str]) -> str | None:
    """构建技能目录 prompt，无技能返回 None。"""
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
        f"  <skill><name>{s.name}</name><description>{s.description}</description><location>{s.location}</location></skill>"
        for s in resolved
    )
    return f"以下技能提供特定任务的专业指令。当任务匹配某个技能的描述时，使用 read_file 工具读取对应 location 的 SKILL.md 加载完整指令。\n\n<available_skills>\n{catalog}\n</available_skills>"


# ---------------------------------------------------------------------------
# agent 节点
# ---------------------------------------------------------------------------


@node_type(
    label="智能代理",
    description="LLM 自动调用工具循环执行，直到无需工具或达到最大迭代次数",
    group=NodeGroup.LLM,
    input_schema={
        "prompt": {"type": "string", "required": True, "description": "用户提示词"},
        "system": {"type": "string", "required": False, "description": "系统提示词"},
        "context": {"type": "string", "required": False, "description": "上下文"},
        "file_paths": {
            "type": "list",
            "required": False,
            "description": "上传文件的路径列表",
        },
        "model": {"type": "string", "required": False, "description": "模型名"},
        "tools": {
            "type": "list",
            "required": False,
            "description": "工具名列表，['*'] 加载全部",
        },
        "skills": {
            "type": "list",
            "required": False,
            "description": "技能名列表，['*'] 加载全部",
        },
        "max_iterations": {
            "type": "integer",
            "required": False,
            "description": "最大循环次数（默认 5）",
        },
    },
    output_schema={
        "type": "object",
        "fields": {
            "content": {"type": "string", "description": "LLM 最终回复文本"},
            "messages": {
                "type": "list",
                "description": "完整会话记录（system/user/assistant/tool 消息）",
            },
        },
    },
)
async def agent(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串")

    model = ctx.get("model") or None
    max_iter = int(ctx.get("max_iterations") or 5)

    # --- 归一化 file_paths：统一为 upload ID 列表 ---
    raw_files = ctx.get("file_paths") or []
    if isinstance(raw_files, dict):
        raw_files = [raw_files]
    file_ids: list[str] = [f["id"] for f in raw_files]

    messages: list[dict[str, str]] = []
    system = ctx.get("system")
    context = ctx.get("context")

    # --- 技能目录注入 system prompt ---
    skill_prompt = _build_skill_prompt(ctx.get("skills") or [])
    if skill_prompt:
        system = f"{skill_prompt}\n\n{system}" if system else skill_prompt

    parts: list[str] = []
    if file_ids:
        # Docker 沙箱用容器内路径，否则用宿主机完整路径
        parts.append("已上传文件：\n" + "\n".join(f"- /uploads/{fid}" for fid in file_ids))
    if isinstance(context, str) and context.strip():
        parts.append(f"参考资料：\n{context}")
    parts.append(f"用户问题：{prompt}")
    user_prompt = "\n\n".join(parts)

    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})

    # --- 构建工具列表（有 skills 时自动注入 read_file + run_code）---
    tool_names: list[str] = ctx.get("tools") or []
    if ctx.get("skills") and "*" not in tool_names:
        for t in ("read_file", "run_code"):
            if t not in tool_names:
                tool_names.append(t)
    tools = _build_tools(tool_names)

    content = ""

    for iteration in range(1, max_iter + 1):
        logger.info("[agent] iteration %d / %d", iteration, max_iter)

        result = await llm_chat_call(model, messages, tools=tools)
        logger.info(result)
        
        content = result["content"]
        tool_calls = result.get("tool_calls", [])

        if not tool_calls:
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            logger.info("[agent] finished after %d iteration(s)", iteration)
            break

        tool_results = await execute_tool_calls(tool_calls)

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })
        for tr in tool_results:
            messages.append({
                "role": "tool",
                "tool_call_id": tr["tool_call_id"],
                "content": f"[{tr['tool_name']}] {tr['output']}",
            })
    else:
        logger.warning("[agent] max iterations (%d) reached", max_iter)

    return {
        "content": content,
        "messages": messages,
    }
