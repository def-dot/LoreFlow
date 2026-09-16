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
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, Field, create_model

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
    input_schema: type[BaseModel] | None = field(default=None, hash=False)
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

def _infer_model(
    func: Callable[..., Any],
    params: dict[str, str] | None = None,
    *,
    skip_prefix: str | None = None,
) -> type[BaseModel] | None:
    """从函数签名 + params 描述推导 Pydantic 输入模型。

    Args:
        func: 目标函数
        params: 参数描述映射 {"参数名": "描述"}
        skip_prefix: 跳过以此前缀开头的参数名（如 "_" 跳过引擎注入参数）。
                     Pydantic 不允许字段名以 "_" 开头，需由调用方显式声明。
    """
    params = params or {}
    fields: dict[str, Any] = {}
    for pname, param in inspect.signature(func).parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if skip_prefix and pname.startswith(skip_prefix):
            continue
        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        desc = params.get(pname, "")
        if param.default is inspect.Parameter.empty:
            fields[pname] = (ann, Field(description=desc))
        else:
            fields[pname] = (ann, Field(default=param.default, description=desc))
    if not fields:
        return None
    return create_model(f"{func.__name__}Input", **fields)


# ---------------------------------------------------------------------------
# @func — 统一注册装饰器
# ---------------------------------------------------------------------------

def func(
    label: str = "",
    description: str = "",
    name: str | None = None,
    metadata: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    output_model: type[BaseModel] | None = None,
    node: bool = True,
    tool: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """统一注册装饰器：同时注册到 REGISTRY 和 TOOL_REGISTRY。

    - ``node=True`` → 注册到 REGISTRY（DAG 引擎可编排）
    - ``tool=True`` → 注册到 TOOL_REGISTRY（LLM agent 可调用）
    - ``params`` 只需 {"参数名": "描述"}，type/required 从签名自动推导
    - ``output_model`` 输出 Pydantic 模型（定义字段结构）

    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        type_name = name or func.__name__
        out_schema: dict[str, Any] | None = None
        if output_model is not None:
            if isinstance(output_model, type) and issubclass(output_model, BaseModel):
                out_schema = {
                    "type": "object",
                    "fields": output_model_to_dict(output_model) or {},
                }
            elif get_origin(output_model) is list:
                item_model = get_args(output_model)[0]
                out_schema = {
                    "type": "list",
                    "item": {"type": "object", "fields": output_model_to_dict(item_model) or {}},
                }

        fd = FuncDef(
            name=type_name,
            func=func,
            label=label,
            description=description,
            metadata=metadata or {},
            input_schema=_infer_model(func, params, skip_prefix="_"),
            output_schema=out_schema,
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
    schema = fd.input_schema.model_json_schema() if fd.input_schema else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {"name": fd.name, "description": fd.description, "parameters": schema},
    }


# ---------------------------------------------------------------------------
# 前端展示格式转换
# ---------------------------------------------------------------------------

_TYPE_TO_SCHEMA: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "list",
    dict: "object",
}



def input_schema_to_dict(model: type[BaseModel] | None) -> dict[str, dict[str, Any]] | None:
    """Pydantic model → 前端 SchemaField 字典格式。"""
    if model is None:
        return None

    schema: dict[str, dict[str, Any]] = {}
    for fname, finfo in model.model_fields.items():
        field_def: dict[str, Any] = {
            "type": _TYPE_TO_SCHEMA.get(finfo.annotation, "string"),
            "required": finfo.is_required(),
        }
        if finfo.description:
            field_def["description"] = finfo.description
        schema[fname] = field_def
    return schema or None


# ---------------------------------------------------------------------------
# 输出 schema
# ---------------------------------------------------------------------------

def _annotation_to_schema(ann: Any) -> dict[str, Any]:
    """类型注解 → SchemaField dict（递归）。"""
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return {"type": "object", "fields": output_model_to_dict(ann) or {}}

    origin = get_origin(ann)
    args = get_args(ann)

    if origin is list:
        item = _annotation_to_schema(args[0]) if args else {"type": "string"}
        return {"type": "list", "item": item}

    if origin is dict:
        return {"type": "object"}

    if origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _annotation_to_schema(non_none[0])

    return {"type": _TYPE_TO_SCHEMA.get(ann, "string")}


def output_model_to_dict(model: type[BaseModel]) -> dict[str, dict[str, Any]] | None:
    """Pydantic model → 前端 SchemaField fields 字典。"""
    fields: dict[str, dict[str, Any]] = {}
    for fname, finfo in model.model_fields.items():
        fd = _annotation_to_schema(finfo.annotation)
        if finfo.description:
            fd["description"] = finfo.description
        if not finfo.is_required():
            fd["required"] = False
        fields[fname] = fd
    return fields or None



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
