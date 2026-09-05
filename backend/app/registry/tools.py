"""
工具注册表 — 为 LLM function calling 提供可执行的工具函数。

- ``TOOL_REGISTRY`` 是全局工具注册表（name → async callable）
- ``@tool`` 装饰器注册工具函数（签名 ``async def(**kwargs) -> str``）
- ``tool_executor`` 节点类型按 ``function.name`` 路由到注册工具并收集结果

插件文件中使用 ``@tool`` 即可注册自定义工具，无需额外配置。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.registry.core import NodeGroup, node_type

# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

#: 全局工具注册表：name → async callable(**kwargs) -> str
TOOL_REGISTRY: dict[str, Callable[..., Any]] = {}


def tool(
    name: str | None = None,
    description: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """工具注册装饰器：将异步函数注册为可被 LLM 调用的工具。

    用法::

        @tool(name="get_weather", description="查询城市天气")
        async def get_weather(city: str) -> str:
            return f"{city}：晴，25°C"

    函数签名接受关键字参数，返回字符串结果。
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or func.__name__
        TOOL_REGISTRY[tool_name] = func
        func.__tool_name__ = tool_name
        func.__tool_description__ = description
        return func
    return decorator


@tool(name="get_weather", description="查询指定城市的当前天气信息")
async def get_weather(city: str = "未知城市") -> str:
    """模拟天气查询 — 替换为真实 API 调用即可。"""
    return f"{city}：晴，气温 25°C，湿度 45%，东南风 3 级"


@tool(name="calculator", description="执行数学计算表达式并返回结果")
async def calculator(expression: str = "") -> str:
    """模拟计算器 — 替换为安全的表达式求值库。"""
    try:
        return str(eval(expression))
    except Exception:
        return f"计算错误：无法计算表达式 {expression}"


# ---------------------------------------------------------------------------
# tool_executor 节点
# ---------------------------------------------------------------------------


@node_type(
    label="工具执行",
    description="按 function.name 路由到注册的工具函数，收集执行结果",
    group=NodeGroup.LLM,
    input_schema={
        "tool_calls": {
            "type": "list",
            "required": True,
            "description": "LLM 返回的工具调用列表（llm_chat 输出的 tool_calls 字段）",
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

        handler = TOOL_REGISTRY.get(name)
        if handler is None:
            output = f"未知工具：{name}（未在 TOOL_REGISTRY 中注册）"
        else:
            try:
                output = await handler(**args)
            except Exception as exc:
                output = f"工具 {name} 执行失败：{type(exc).__name__}: {exc}"

        results.append({
            "tool_name": name,
            "arguments": args,
            "output": str(output),
        })

    return results
