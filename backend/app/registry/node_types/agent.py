"""
agent 节点 — LLM + 工具自动循环，直到无需工具或达到最大迭代次数。
"""

from __future__ import annotations

import logging
from typing import Any

from app.registry.node_type import NodeGroup, node_type
from app.services.llm import llm_chat_call
from app.registry.tool import execute_tool_call
from app.services.agent_tools import build_skill_prompt, build_tools

logger = logging.getLogger(__name__)


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
    context = ctx.get("context")

    parts: list[str] = []
    if file_ids:
        # Docker 沙箱用容器内路径，否则用宿主机完整路径
        parts.append("已上传文件：\n" + "\n".join(f"- /uploads/{fid}" for fid in file_ids))
    if isinstance(context, str) and context.strip():
        parts.append(f"参考资料：\n{context}")
    parts.append(f"用户问题：{prompt}")
    messages.append({"role": "user", "content": "\n\n".join(parts)})

    # --- 技能目录 + system prompt 注入首条 ---
    system = ctx.get("system")
    skills = ctx.get("skills")
    if system or skills:
        skill_prompt = build_skill_prompt(skills) or ""
        system_content = (system or "") + "\n\n" + skill_prompt
        messages.insert(0, {"role": "system", "content": system_content.strip()})

    # --- 构建工具列表（有 skills 时自动注入 read_file + run_code）---
    tool_names: list[str] = list(ctx.get("tools") or [])
    if ctx.get("skills") and "*" not in tool_names:
        for t in ("read_file", "run_code"):
            if t not in tool_names:
                tool_names.append(t)
    tools = build_tools(tool_names)

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

        tool_results = [await execute_tool_call(tc) for tc in tool_calls]

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
