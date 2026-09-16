"""
注册表核心 — FuncDef 数据类、@func 装饰器与双注册表。

- ``FuncDef`` 统一函数定义（节点类型和工具共用）
- ``REGISTRY`` — 节点注册表（DAG 引擎）
- ``TOOL_REGISTRY`` — 工具注册表（LLM Agent）
- ``@func`` 装饰器同时注册两侧（node=True / tool=True）
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

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
    output_schema: type[BaseModel] | None = field(default=None, hash=False)


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
) -> type[BaseModel] | None:
    """从函数签名 + params 描述推导 Pydantic 输入模型。"""
    params = params or {}
    fields: dict[str, Any] = {}
    for pname, param in inspect.signature(func).parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if pname.startswith("_"):
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

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        type_name = name or fn.__name__
        fd = FuncDef(
            name=type_name,
            func=fn,
            label=label,
            description=description,
            metadata=metadata or {},
            input_schema=_infer_model(fn, params),
            output_schema=output_model,
        )

        if node:
            REGISTRY[type_name] = fd
        if tool:
            TOOL_REGISTRY[type_name] = fd

        setattr(fn, "__func_def__", fd)
        return fn

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
# 前端展示格式转换（标准 JSON Schema）
# ---------------------------------------------------------------------------


def input_schema_to_dict(model: type[BaseModel] | None) -> dict[str, Any] | None:
    """Pydantic model → 标准 JSON Schema（供前端解析）。"""
    if model is None:
        return None
    return model.model_json_schema()


def output_schema_to_dict(model: type[BaseModel] | None) -> dict[str, Any] | None:
    """Pydantic model → 标准 JSON Schema（供前端解析）。"""
    if model is None:
        return None
    return model.model_json_schema()
