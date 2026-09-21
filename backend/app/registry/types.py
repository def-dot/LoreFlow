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
    """一个可被 YAML 引用或 LLM 调用的函数定义。"""

    name: str
    func: Callable[..., Any]
    label: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    input_schema: type[BaseModel] | None = None
    output_schema: type[BaseModel] | None = None


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
    """从函数签名推导 (input_schema, output_schema)。

    使用 typing.get_type_hints() 解析前向引用（from __future__ import annotations）。
    """
    _is_model = lambda ann: isinstance(ann, type) and issubclass(ann, BaseModel)
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}

    # input_schema: 第一个 BaseModel 参数
    sig = inspect.signature(func)
    input_schema = None
    for pname in sig.parameters:
        ann = hints.get(pname)
        if ann is not None and _is_model(ann):
            input_schema = ann
            break

    # output_schema: 返回值
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
