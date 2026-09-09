"""
tool_executor 节点 — 接收 LLM 的工具调用请求，路由到对应工具执行并返回结果。
"""

import json
from typing import Any

from app.registry.node_type import NodeGroup, node_type
from app.registry.tool import TOOL_REGISTRY


@node_type(
    label="工具执行",
    description="接收 LLM 的工具调用请求，路由到对应工具执行并返回结果",
    group=NodeGroup.LLM,
    input_schema={
        "tool_calls": {
            "type": "list",
            "required": True,
            "description": "LLM 返回的工具调用列表（tool_calls 字段）",
            "item": {
                "type": "object",
                "fields": {
                    "function": {
                        "type": "object",
                        "fields": {
                            "name": {"type": "string", "description": "工具名称"},
                            "arguments": {"type": "object", "description": "调用参数"},
                        },
                    },
                },
            },
        },
    },
    output_schema={
        "type": "list",
        "item": {
            "type": "object",
            "fields": {
                "tool_name": {"type": "string", "description": "工具名称"},
                "arguments": {"type": "object", "description": "调用参数"},
                "output": {"type": "string", "description": "执行结果"},
            },
        },
    },
)
async def tool_executor(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    tool_calls = ctx.get("tool_calls")
    if not isinstance(tool_calls, list):
        raise ValueError("缺少工具调用列表：tool_calls 必须是列表")

    results: list[dict[str, Any]] = []
    for tc in tool_calls:
        func_def = tc.get("function", {})
        name = func_def.get("name", "")
        args = func_def.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}

        td = TOOL_REGISTRY.get(name)
        if td is None:
            output = f"未知工具：{name}（未在 TOOL_REGISTRY 中注册）"
        else:
            try:
                output = await td.func(**args)
            except Exception as exc:
                output = f"工具 {name} 执行失败：{type(exc).__name__}: {exc}"

        results.append({
            "tool_name": name,
            "arguments": args,
            "output": str(output),
        })
    return results
