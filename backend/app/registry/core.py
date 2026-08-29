"""
注册表核心 — NodeType 数据类、``@node`` 注册装饰器与全局注册表。

本模块是叶子模块（不 import 应用内其他模块），供 ``functions.py``
在函数定义处声明元信息；``__init__.py`` 只做纯再导出。
human 结构化类型在 :mod:`app.registry.human` 注册（协议函数本体）。

``REGISTRY`` 是唯一的注册表本体（``name -> NodeType``），其余模块
直接读写该字典：``@node`` 装饰器负责注册，插件加载器负责清理。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NodeType:
    """一个可被 YAML 引用的类型：节点函数或条件谓词。

    ``func`` 签名统一为 ``(ctx: dict) -> Any``
    """

    name: str
    func: Callable[..., Any]
    label: str
    description: str


#: 全局注册表：name -> NodeType。导入 functions.py 时由 @node 自动填充，
#: 其余模块直接读写该字典。
REGISTRY: dict[str, NodeType] = {}


def node(
    label: str,
    description: str,
    name: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """节点函数注册装饰器：在函数定义处声明元信息并自动注册。

    返回原函数本身（仅附加 ``__node_type__`` 属性），因此对
    async 函数、docstring、``__name__`` 均无影响。
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        node_name = name or func.__name__
        node_type = NodeType(
            name=node_name,
            func=func,
            label=label,
            description=description,
        )
        # 1. 自动注册到全局注册表
        REGISTRY[node_name] = node_type
        # 2. 元数据绑定到函数对象本身，便于从函数侧反查
        setattr(func, "__node_type__", node_type)
        return func

    return decorator


def unregister(name: str) -> NodeType | None:
    """从全局注册表删除一个节点。
    """
    return REGISTRY.pop(name, None)
