"""
工具注册表 — 为 LLM function calling 提供工具定义与执行能力。

- ``TOOL_REGISTRY`` 是全局工具注册表（name → async callable）
- ``@tool`` 装饰器注册工具函数（签名 ``async def(**kwargs) -> str``）
- ``tool_executor`` 节点类型按 ``function.name`` 路由到注册工具并收集结果

插件文件中使用 ``@tool`` 即可注册自定义工具，无需额外配置。
"""

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.registry.core import NodeGroup, node_type

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamDef:
    """工具参数定义。"""

    name: str
    param_type: type = str
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class ToolDef:
    """一个可被 LLM 调用的工具定义。"""

    name: str
    func: Callable[..., Any]
    description: str = ""
    params: list[ParamDef] = field(default_factory=list)


#: 全局工具注册表：name → ToolDef
TOOL_REGISTRY: dict[str, ToolDef] = {}


def tool(
    name: str | None = None,
    description: str = "",
    params: dict[str, str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """工具注册装饰器：将异步函数注册为可被 LLM 调用的工具。

    用法::

        @tool(name="get_weather", description="查询城市天气",
              params={"city": "城市名称，如北京、上海"})
        async def get_weather(city: str) -> str:
            return f"{city}：晴，25°C"

    参数类型从函数签名自动提取（支持 str/int/float/bool），
    描述通过 ``params`` 传入。
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or func.__name__
        descriptions = params or {}
        param_defs: list[ParamDef] = []
        try:
            sig = inspect.signature(func)
            for pname, param in sig.parameters.items():
                annotation = param.annotation if param.annotation is not inspect.Parameter.empty else str
                param_defs.append(ParamDef(
                    name=pname,
                    param_type=annotation,
                    description=descriptions.get(pname, ""),
                    required=param.default is inspect.Parameter.empty,
                ))
        except Exception as exc:
            logger.warning("工具 %s 参数解析失败：%s", tool_name, exc)
        td = ToolDef(name=tool_name, func=func, description=description, params=param_defs)
        TOOL_REGISTRY[tool_name] = td
        setattr(func, "__tool_def__", td)
        return func
    return decorator


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
    return await execute_tool_calls(tool_calls)


async def execute_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """执行工具调用列表，返回结果。"""
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
