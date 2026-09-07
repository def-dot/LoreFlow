"""
工具注册表与 agent 节点 — 为 LLM function calling 提供工具执行能力。

- ``TOOL_REGISTRY`` 是全局工具注册表（name → async callable）
- ``@tool`` 装饰器注册工具函数（签名 ``async def(**kwargs) -> str``）
- ``tool_executor`` 节点类型按 ``function.name`` 路由到注册工具并收集结果
- ``agent`` 节点类型封装 LLM + 工具循环，自动多轮调用直到完成

插件文件中使用 ``@tool`` 即可注册自定义工具，无需额外配置。
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_type_hints

from app.utils.http import http_client
from app.core.config import settings
from app.registry.core import NodeGroup, node_type
from app.registry.llm import _ollama_chat

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 工具注册表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolDef:
    """一个可被 LLM 调用的工具定义。"""

    name: str
    func: Callable[..., Any]
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


#: 全局工具注册表：name → ToolDef
TOOL_REGISTRY: dict[str, ToolDef] = {}

#: Python 类型 → JSON Schema 类型
_TYPE_MAP = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _build_param_schema(
    func: Callable[..., Any],
    descriptions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """从函数签名自动生成 JSON Schema parameters。

    Args:
        func: 工具函数
        descriptions: 参数名 → 中文描述（来自 @tool 的 params 参数）
    """
    try:
        sig = inspect.signature(func)
        hints = get_type_hints(func)
    except Exception:
        return {}

    descriptions = descriptions or {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        type_hint = hints.get(pname, str)
        prop: dict[str, Any] = {"type": _TYPE_MAP.get(type_hint, "string")}
        if pname in descriptions:
            prop["description"] = descriptions[pname]
        props[pname] = prop
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    if not props:
        return {}
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


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
        td = ToolDef(
            name=tool_name,
            func=func,
            description=description,
            parameters=_build_param_schema(func, params),
        )
        TOOL_REGISTRY[tool_name] = td
        setattr(func, "__tool_def__", td)
        return func
    return decorator


@tool(name="get_weather", description="查询指定城市的当前天气信息",
      params={"city": "城市名称，如北京、上海"})
async def get_weather(city: str) -> str:
    """通过 wttr.in 查询实时天气。"""

    try:
        resp = await http_client().get(f"https://wttr.in/{city}", params={"format": "j1"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cur = data["current_condition"][0]
        desc = cur.get("lang_zh", [{}])[0].get("value") or cur.get("weatherDesc", [{}])[0].get("value", "")
        temp = cur.get("temp_C", "?")
        humidity = cur.get("humidity", "?")
        wind = cur.get("windspeedKmph", "?")
        wind_dir = cur.get("winddir16Point", "")
        return f"{city}：{desc}，气温 {temp}°C，湿度 {humidity}%，风速 {wind} km/h {wind_dir}"
    except Exception as exc:
        return f"{city}：天气查询失败（{type(exc).__name__}: {exc}）"


@tool(name="calculator", description="执行数学计算表达式并返回结果",
      params={"expression": "数学表达式，如 123 * 456、sqrt(144)"})
async def calculator(expression: str = "") -> str:
    """计算器 — 替换为安全的表达式求值库。"""
    try:
        return str(eval(expression))
    except Exception:
        return f"计算错误：无法计算表达式 {expression}"


# ---------------------------------------------------------------------------
# 工具定义 → Ollama function calling 格式
# ---------------------------------------------------------------------------


def get_tool_definitions(names: list[str]) -> list[dict[str, Any]]:
    """从 TOOL_REGISTRY 按名称查找工具，返回 Ollama function calling 格式定义列表。

    YAML 中 ``tools: [get_weather, calculator]`` 经此函数展开为完整 schema。
    """
    defs: list[dict[str, Any]] = []
    for n in names:
        td = TOOL_REGISTRY.get(n)
        if td is None:
            raise ValueError(f"未知工具：{n}（未在 TOOL_REGISTRY 中注册）")
        defs.append({
            "type": "function",
            "function": {
                "name": td.name,
                "description": td.description,
                "parameters": td.parameters,
            },
        })
    return defs


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


# ---------------------------------------------------------------------------
# agent 节点 — LLM + 工具自动循环
# ---------------------------------------------------------------------------


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
        tools = get_tool_definitions(tools_input) if isinstance(tools_input[0], str) else tools_input
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
        tool_results = await _execute_tool_calls(tool_calls)
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


async def _execute_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """执行工具调用列表，返回结果（复用 tool_executor 的路由逻辑）。"""
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
