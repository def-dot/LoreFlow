"""
agent 节点 — LLM + 工具自动循环，直到无需工具或达到最大迭代次数。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from app.registry.types import node
from app.services.llm import llm_chat_call
from app.services.agent_tools import build_skill_prompt, build_tools, execute_tool_call

logger = logging.getLogger(__name__)


class AgentParams(BaseModel):
    prompt: str = Field(description="用户提示词", min_length=1)
    system: str | None = Field(default=None, description="系统提示词")
    model: str | None = Field(default=None, description="模型名")
    tools: list[str] = Field(default_factory=list, description="工具名列表")
    skills: list[str] = Field(default_factory=list, description="技能名列表")
    max_iterations: int = Field(default=10, description="最大循环次数")


class AgentOutput(BaseModel):
    content: str = Field(description="LLM 最终回复文本")
    messages: list[dict[str, Any]] = Field(description="完整会话记录")


@node(
    label="智能代理",
    description="LLM 自动调用工具循环执行，直到无需工具或达到最大迭代次数",
    metadata={"group": "LLM", "order": 10},
)
async def agent(params: AgentParams) -> AgentOutput:
    if params.skills and "load_skill" not in params.tools:
        params.tools.append("load_skill")
    tool_defs = build_tools(params.tools)

    # --- system prompt ---
    system_parts = [params.system, build_skill_prompt(params.skills)]
    system_content = "\n\n".join(p for p in system_parts if p)

    messages: list[dict[str, Any]] = []
    if system_content:
        messages.append({"role": "system", "content": system_content})
    messages.append({"role": "user", "content": params.prompt})

    content = ""
    for i in range(params.max_iterations):
        logger.info("[agent] iteration %d / %d", i + 1, params.max_iterations)

        result = await llm_chat_call(params.model, messages, tools=tool_defs)
        content = result["content"]
        tool_calls = result.get("tool_calls", [])

        if not tool_calls:
            messages.append({"role": "assistant", "content": content})
            break

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            tr = await execute_tool_call(tc)
            logger.info("[agent] tool %s -> %s", tr["tool_name"], tr["status"])
            messages.append({"role": "tool", "tool_call_id": tr["tool_call_id"], "content": tr["output"]})
    else:
        logger.warning("[agent] max iterations (%d) reached", params.max_iterations)

    return AgentOutput(content=content, messages=messages)
