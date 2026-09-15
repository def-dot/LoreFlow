"""
注册表核心 — FuncDef 数据类、@func 装饰器与双注册表。

- ``FuncDef`` 统一函数定义（节点类型和工具共用）
- ``REGISTRY`` — 节点注册表（DAG 引擎）
- ``TOOL_REGISTRY`` — 工具注册表（LLM Agent）
- ``@func`` 装饰器同时注册两侧（node=True / tool=True）
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 统一函数定义
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FuncDef:
    """一个可被 YAML 引用或 LLM 调用的函数定义。"""

    name: str
    func: Callable[..., Any]
    label: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict, hash=False)
    input_schema: dict[str, dict[str, Any]] | None = field(default=None, hash=False)
    output_schema: dict[str, Any] | None = field(default=None, hash=False)


# ---------------------------------------------------------------------------
# 双注册表
# ---------------------------------------------------------------------------

#: 节点注册表：name → FuncDef（DAG 引擎 / 节点扫描）
REGISTRY: dict[str, FuncDef] = {}

#: 工具注册表：name → FuncDef（LLM Agent 工具列表）
TOOL_REGISTRY: dict[str, FuncDef] = {}


# ---------------------------------------------------------------------------
# 参数推导
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "list",
    dict: "object",
}


def _infer_input_schema(
    func: Callable[..., Any],
    params: dict[str, str] | None,
) -> dict[str, dict[str, Any]] | None:
    """从函数签名 + params 描述推导 input_schema。"""
    sig = inspect.signature(func)
    if "ctx" in sig.parameters:
        return None

    params = params or {}
    schema: dict[str, dict[str, Any]] = {}
    for pname, param in sig.parameters.items():
        if pname == "ctx":
            continue
        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        ptype = _TYPE_MAP.get(ann, "string")
        required = param.default is inspect.Parameter.empty
        field_def: dict[str, Any] = {"type": ptype, "required": required}
        if pname in params:
            field_def["description"] = params[pname]
        schema[pname] = field_def
    return schema or None


# ---------------------------------------------------------------------------
# @func — 统一注册装饰器
# ---------------------------------------------------------------------------

def func(
    label: str = "",
    description: str = "",
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    input_schema: dict[str, dict[str, Any]] | None = None,
    output_schema: dict[str, Any] | None = None,
    node: bool = True,
    tool: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """统一注册装饰器：同时注册到 REGISTRY 和 TOOL_REGISTRY。

    - ``node=True`` → 注册到 REGISTRY（DAG 引擎可编排）
    - ``tool=True`` → 注册到 TOOL_REGISTRY（LLM agent 可调用）
    - ``params`` 只需 {"参数名": "描述"}，type/required 从签名自动推导
    - ``input_schema`` 手动覆盖推导结果（ctx 模式等无法推导的场景）

    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        type_name = name or func.__name__
        inferred = input_schema if input_schema is not None else _infer_input_schema(func, params)
        fd = FuncDef(
            name=type_name,
            func=func,
            label=label,
            description=description,
            metadata=metadata or {},
            input_schema=inferred,
            output_schema=output_schema,
        )

        if node:
            REGISTRY[type_name] = fd
        if tool:
            TOOL_REGISTRY[type_name] = fd

        setattr(func, "__func_def__", fd)
        return func

    return decorator



# ---------------------------------------------------------------------------
# 注销
# ---------------------------------------------------------------------------

def unregister(name: str) -> FuncDef | None:
    """从节点注册表删除一个节点。"""
    return REGISTRY.pop(name, None)


def unregister_tool(name: str) -> FuncDef | None:
    """从工具注册表删除一个工具。"""
    return TOOL_REGISTRY.pop(name, None)


# ---------------------------------------------------------------------------
# OpenAI function calling 格式转换
# ---------------------------------------------------------------------------

def input_schema_to_openai(fd: FuncDef) -> dict[str, Any]:
    """FuncDef.input_schema → OpenAI function calling 格式。"""
    schema: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    for pname, fdef in (fd.input_schema or {}).items():
        prop: dict[str, Any] = {"type": fdef.get("type", "string")}
        if "description" in fdef:
            prop["description"] = fdef["description"]
        schema["properties"][pname] = prop
        if fdef.get("required", False):
            schema["required"].append(pname)
    return {
        "type": "function",
        "function": {"name": fd.name, "description": fd.description, "parameters": schema},
    }


# ---------------------------------------------------------------------------
# 工具执行
# ---------------------------------------------------------------------------

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