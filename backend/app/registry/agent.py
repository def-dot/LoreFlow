"""
agent 节点 — LLM + 工具自动循环，直到无需工具或达到最大迭代次数。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.registry.core import NodeGroup, node_type
from app.registry.llm import _ollama_chat
from app.registry.tools import TOOL_REGISTRY, execute_tool_calls

logger = logging.getLogger(__name__)

_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


@node_type(
    label="智能代理",
    description="LLM 自动调用工具循环执行，直到无需工具或达到最大迭代次数",
    group=NodeGroup.LLM,
    input_schema={
        "prompt": {"type": "string", "required": True, "description": "用户提示词"},
        "system": {"type": "string", "required": False, "description": "系统提示词"},
        "context": {"type": "string", "required": False, "description": "上下文"},
        "model": {"type": "string", "required": False, "description": "模型名"},
        "tools": {
            "type": "list",
            "required": False,
            "description": "工具名列表（如 [get_weather, calculator]）或完整 Ollama 定义列表",
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
            "iterations": {"type": "integer", "description": "实际迭代次数"},
            "all_tool_calls": {
                "type": "list",
                "description": "所有迭代中执行的工具调用记录",
            },
        },
    },
)
async def agent(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串")

    model = str(ctx.get("model") or settings.OLLAMA_MODEL)
    max_iter = int(ctx.get("max_iterations") or 5)

    # 构建初始消息
    messages: list[dict[str, str]] = []
    system = ctx.get("system")
    context = ctx.get("context")
    user_prompt = prompt
    if isinstance(context, str) and context.strip():
        user_prompt = f"参考资料：\n{context}\n\n用户问题：{prompt}"
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})

    # 工具定义：支持字符串列表（工具名）或完整定义列表（向后兼容）
    tools_input = ctx.get("tools")
    if isinstance(tools_input, list) and tools_input:
        if isinstance(tools_input[0], str):
            tools = []
            for n in tools_input:
                td = TOOL_REGISTRY.get(n)
                if td is None:
                    raise ValueError(f"未知工具：{n}（未在 TOOL_REGISTRY 中注册）")
                props = {p.name: {"type": _TYPE_MAP.get(p.param_type, "string")} for p in td.params}
                for p in td.params:
                    if p.description:
                        props[p.name]["description"] = p.description
                required = [p.name for p in td.params if p.required]
                schema: dict[str, Any] = {"type": "object", "properties": props}
                if required:
                    schema["required"] = required
                tools.append({
                    "type": "function",
                    "function": {"name": td.name, "description": td.description, "parameters": schema},
                })
        else:
            tools = tools_input
    else:
        tools = None

    # 循环执行
    all_tool_calls: list[dict[str, Any]] = []
    content = ""

    for iteration in range(1, max_iter + 1):
        logger.info("[agent] iteration %d / %d", iteration, max_iter)

        result = await _ollama_chat(model, messages, tools=tools)
        content = result["content"]
        tool_calls = result.get("tool_calls", [])

        # 没有工具调用 → 结束
        if not tool_calls:
            logger.info("[agent] finished after %d iteration(s)", iteration)
            break

        # 执行工具调用
        tool_results = await execute_tool_calls(tool_calls)
        all_tool_calls.extend(tool_results)

        # 将 LLM 回复和工具结果追加到消息历史
        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": tool_calls,
        })
        for tr in tool_results:
            messages.append({
                "role": "tool",
                "content": str(tr["output"]),
            })
    else:
        logger.warning("[agent] max iterations (%d) reached", max_iter)

    return {
        "content": content,
        "iterations": min(iteration, max_iter),
        "all_tool_calls": all_tool_calls,
    }
