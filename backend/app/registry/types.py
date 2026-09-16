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

from pydantic import BaseModel

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
    sig = inspect.signature(func)
    input_schema = next(
        (p.annotation for p in sig.parameters.values() if _is_model(p.annotation)),
        None,
    )
    output_schema = sig.return_annotation if _is_model(sig.return_annotation) else None
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
    """从工具注册表删除一个工具。"""
    return TOOL_REGISTRY.pop(name, None)
