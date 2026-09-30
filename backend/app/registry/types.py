"""
注册表核心 — FuncDef、@func 装饰器与双注册表。

- ``FuncDef`` 统一函数定义（节点类型和工具共用）
- ``REGISTRY`` — 节点注册表（DAG 引擎）
- ``TOOL_REGISTRY`` — 工具注册表（LLM Agent）
- ``@func`` 装饰器同时注册两侧（node=True / tool=True）
"""

from __future__ import annotations

import inspect
import logging
import typing
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 统一函数定义
# ---------------------------------------------------------------------------

@dataclass
class FuncDef:
    """一个可被 YAML 引用或 LLM 调用的函数定义。

    ``input_schema`` / ``output_schema`` 二选一（一份签名，只取一种形态）：
    - ``type[BaseModel]`` — 本地 ``@func`` 从签名推导的模型
    - ``dict[str, Any]`` — JSON Schema 原文（如 MCP ``inputSchema`` / ``outputSchema``），无损透传
    """

    name: str
    func: Callable[..., Any]
    label: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    input_schema: type[BaseModel] | dict[str, Any] | None = None
    output_schema: type[BaseModel] | dict[str, Any] | None = None

    def json_input_schema(self) -> dict[str, Any] | None:
        """对外 JSON Schema（LLM tools / 前端表单）。无输入声明时返回 ``None``。

        dict 形态原样透传（无损）；模型形态由 pydantic 导出。
        """
        s = self.input_schema
        if isinstance(s, dict):
            return s
        return s.model_json_schema() if s is not None else None

    def json_output_schema(self) -> dict[str, Any] | None:
        """对外输出 JSON Schema。无输出声明时返回 ``None``。"""
        s = self.output_schema
        if isinstance(s, dict):
            return s
        return s.model_json_schema() if s is not None else None

    async def invoke(self, args: dict[str, Any]) -> Any:
        """统一调用入口：模型形态传实例，dict 形态拆 kwargs，无声明则无参调用。"""
        s = self.input_schema
        if isinstance(s, dict):
            return await self.func(**args)
        if s is not None:
            return await self.func(s(**args))
        return await self.func()


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

#: 节点注册表：name → FuncDef（DAG 引擎 / 节点扫描）
REGISTRY: dict[str, FuncDef] = {}

#: 工具注册表：name → FuncDef（LLM Agent 工具列表）
TOOL_REGISTRY: dict[str, FuncDef] = {}


# ---------------------------------------------------------------------------
# 参数推导
# ---------------------------------------------------------------------------

def _infer_func_schema(func: Callable[..., Any]) -> tuple[type[BaseModel] | None, type[BaseModel] | None]:
    """从函数签名推导 (input_schema, output_schema)。"""
    _is_model = lambda ann: isinstance(ann, type) and issubclass(ann, BaseModel)
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}

    sig = inspect.signature(func)
    input_schema = None
    for pname in sig.parameters:
        ann = hints.get(pname)
        if ann is not None and _is_model(ann):
            input_schema = ann
            break

    ret = hints.get("return")
    output_schema = ret if ret is not None and _is_model(ret) else None
    return input_schema, output_schema


def func(
    label: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
    node: bool = True,
    tool: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """统一注册装饰器：同时注册到 REGISTRY 和 TOOL_REGISTRY。
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        input_schema, output_schema = _infer_func_schema(fn)
        fd = FuncDef(
            name=fn.__name__,
            func=fn,
            label=label,
            description=description,
            metadata=metadata or {},
            input_schema=input_schema,
            output_schema=output_schema,
        )

        if node:
            REGISTRY[fd.name] = fd
        if tool:
            TOOL_REGISTRY[fd.name] = fd

        setattr(fn, "__func_def__", fd)
        return fn

    return decorator


def unregister(name: str) -> FuncDef | None:
    """从节点注册表删除一个节点。"""
    return REGISTRY.pop(name, None)


def unregister_tool(name: str) -> FuncDef | None:
    """从工具注册表删除一个节点。"""
    return TOOL_REGISTRY.pop(name, None)


# ---------------------------------------------------------------------------
# 用途 / 来源标注（供 API 层展示）
# ---------------------------------------------------------------------------

def roles_of(name: str) -> list[str]:
    """该名字暴露给哪些消费方：node = 工作流，tool = Agent。"""
    roles: list[str] = []
    if name in REGISTRY:
        roles.append("node")
    if name in TOOL_REGISTRY:
        roles.append("tool")
    return roles


def source_of(fd: FuncDef, owner: str = "") -> tuple[str, str]:
    """返回 (kind, name)：builtin / plugin / mcp。

    ``owner`` 是插件文件名（可选）；MCP 来源由注册时写入的 metadata 判定。
    """
    meta = fd.metadata or {}
    if meta.get("source") == "mcp":
        return "mcp", str(meta.get("source_name") or meta.get("group") or "")
    if owner:
        return "plugin", owner
    return "builtin", ""
