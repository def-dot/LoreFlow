"""
agent 节点 — LLM + 工具自动循环，直到无需工具或达到最大迭代次数。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.registry.types import func
from app.services.llm import llm_chat_call
from app.services.agent_tools import build_skill_prompt, build_tools, execute_tool_call

logger = logging.getLogger(__name__)


class AgentParams(BaseModel):
    prompt: str = Field(description="用户提示词", min_length=1)
    system: str | None = Field(default=None, description="系统提示词")
    context: str | None = Field(default=None, description="上下文")
    file_names: list[str] | None = Field(default=None, description="上传文件列表")
    model: str | None = Field(default=None, description="模型名")
    tools: list[str] | None = Field(default=None, description="工具名列表，['*'] 加载全部")
    skills: list[str] | None = Field(default=None, description="技能名列表，['*'] 加载全部")
    max_iterations: int = Field(default=5, description="最大循环次数")


class AgentOutput(BaseModel):
    content: str = Field(description="LLM 最终回复文本")
    messages: list[dict[str, Any]] = Field(description="完整会话记录")


@func(
    label="智能代理",
    description="LLM 自动调用工具循环执行，直到无需工具或达到最大迭代次数",
    metadata={"group": "LLM", "order": 10},
)
async def agent(params: AgentParams) -> AgentOutput:
    max_iter = params.max_iterations or 5

    # --- 归一化 file_paths：统一为 upload ID 列表 ---
    file_names = params.file_names or []
  
    messages: list[dict[str, str]] = []

    parts: list[str] = []
    if file_names:
        parts.append("已上传文件：\n" + "\n".join(f"- /uploads/{fid}" for fid in file_names))
    if isinstance(params.context, str) and params.context.strip():
        parts.append(f"参考资料：\n{params.context}")
    parts.append(f"用户问题：{params.prompt}")
    messages.append({"role": "user", "content": "\n\n".join(parts)})

    # --- 技能目录 + system prompt 注入首条 ---
    if params.system or params.skills:
        skill_prompt = build_skill_prompt(params.skills) or ""
        system_content = (params.system or "") + "\n\n" + skill_prompt
        messages.insert(0, {"role": "system", "content": system_content.strip()})

    # --- 构建工具列表（有 skills 时自动注入 load_skill）---
    tool_names: list[str] = list(params.tools or [])
    if params.skills and "*" not in tool_names and "load_skill" not in tool_names:
        tool_names.append("load_skill")
    tool_defs = build_tools(tool_names)

    content = ""

    for iteration in range(1, max_iter + 1):
        logger.info("[agent] iteration %d / %d", iteration, max_iter)

        result = await llm_chat_call(params.model, messages, tools=tool_defs)
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

    return AgentOutput(content=content, messages=messages)
