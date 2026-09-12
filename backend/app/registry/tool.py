"""
工具注册表 — 为 LLM function calling 提供工具定义与执行能力。

- ``TOOL_REGISTRY`` 是全局工具注册表（name → async callable）
- ``@tool`` 装饰器注册工具函数（签名 ``async def(**kwargs) -> str``）

插件文件中使用 ``@tool`` 即可注册自定义工具，无需额外配置。
"""

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamDef:
    """工具参数定义。"""

    name: str
    param_type: str = "string"
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
    _TYPE_TO_STR: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

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
                    param_type=_TYPE_TO_STR.get(annotation, "string"),
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
            output = await td.func(**args)
        except Exception as exc:
            output = f"工具 {name} 执行失败：{type(exc).__name__}: {exc}"
            status = "error"
    else:
        # 尝试 Pipeline 工作流
        from app.services.agent_tools import _resolve_pipeline_tool

        ptd = _resolve_pipeline_tool(name)
        if ptd is not None:
            try:
                output = await ptd.func(**args)
            except Exception as exc:
                output = f"工作流 {name} 执行失败：{type(exc).__name__}: {exc}"
                status = "error"
        else:
            output = f"未知工具：{name}"
            status = "error"

    return {
        "tool_call_id": tc.get("id", ""),
        "tool_name": name,
        "arguments": args,
        "output": str(output),
        "status": status,
    }
