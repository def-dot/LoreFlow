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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 统一函数定义
# ---------------------------------------------------------------------------

@dataclass
class FuncDef:
    """一个可被 YAML 引用或 LLM 调用的函数定义。

    ``input_schema`` / ``output_schema`` 统一为 JSON Schema dict（无损透传）。
    注册时 BaseModel 自动转为 ``model_json_schema()``；MCP / Pipeline 已是 dict。
    """

    name: str
    func: Callable[..., Any]
    label: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    async def invoke(self, args: dict[str, Any]) -> Any:
        """统一调用入口：自动构造 BaseModel 参数，否则拆 kwargs，无声明则无参调用。"""
        if not args and self.input_schema is None:
            return await self.func()

        # 找第一个 BaseModel 参数，自动构造；否则直接拆 kwargs
        try:
            hints = typing.get_type_hints(self.func)
        except Exception:
            hints = {}
        for pname in inspect.signature(self.func).parameters:
            ann = hints.get(pname)
            if ann is not None and isinstance(ann, type) and issubclass(ann, BaseModel):
                return await self.func(**{pname: ann(**args)})

        return await self.func(**args)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

#: 节点注册表：name → FuncDef（DAG 引擎 / 节点扫描）
REGISTRY: dict[str, FuncDef] = {}

#: 工具注册表：name → FuncDef（LLM Agent 工具列表）
TOOL_REGISTRY: dict[str, FuncDef] = {}


def _build_funcdef(
    fn: Callable[..., Any],
    label: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> FuncDef:
    """从函数构建 FuncDef（提取 input_schema / output_schema）。"""
    try:
        hints = typing.get_type_hints(fn)
    except Exception:
        hints = {}

    input_schema = None
    for pname in inspect.signature(fn).parameters:
        ann = hints.get(pname)
        if ann is not None and isinstance(ann, type) and issubclass(ann, BaseModel):
            input_schema = ann.model_json_schema()
            break

    ret = hints.get("return")
    output_schema = ret.model_json_schema() if isinstance(ret, type) and issubclass(ret, BaseModel) else None

    return FuncDef(
        name=fn.__name__,
        func=fn,
        label=label,
        description=description,
        metadata=metadata or {},
        input_schema=input_schema,
        output_schema=output_schema,
    )


def func(
    label: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
    node: bool = True,
    tool: bool = True,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """统一注册装饰器：同时注册到 REGISTRY 和 TOOL_REGISTRY（向后兼容）。"""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fd = _build_funcdef(fn, label, description, metadata)
        if node:
            REGISTRY[fd.name] = fd
        if tool:
            TOOL_REGISTRY[fd.name] = fd
        setattr(fn, "__func_def__", fd)
        return fn

    return decorator


def node(
    label: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册为 DAG 节点（仅 REGISTRY）。"""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fd = _build_funcdef(fn, label, description, metadata)
        REGISTRY[fd.name] = fd
        setattr(fn, "__func_def__", fd)
        return fn

    return decorator


def tool(
    label: str = "",
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册为 Agent 工具（仅 TOOL_REGISTRY）。"""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fd = _build_funcdef(fn, label, description, metadata)
        TOOL_REGISTRY[fd.name] = fd
        setattr(fn, "__func_def__", fd)
        return fn

    return decorator