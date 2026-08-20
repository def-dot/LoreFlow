"""
注册表核心 — NodeType 数据类、``@node`` 注册装饰器与全局注册表。

``REGISTRY`` 是唯一的注册表本体（``name -> NodeType``），其余模块
直接使用它；``register``/``unregister`` 用于运行期追加动态类型。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

NodeKind = Literal["function", "condition"]


@dataclass(frozen=True)
class NodeType:
    """一个可被 YAML 引用的类型：节点函数或条件谓词。

    ``func`` 签名统一为 ``(ctx: dict) -> Any``；``builtin`` 标记
    ``@node`` 装饰器注册的系统内置类型（unregister 不可撤销）。
    """

    name: str
    func: Callable[..., Any]
    kind: NodeKind
    label: str
    description: str
    builtin: bool = False


#: 全局注册表：name -> NodeType。导入 functions.py 时由 @node 自动填充，
#: 其余模块直接读取该字典。
REGISTRY: dict[str, NodeType] = {}


def node(
    label: str,
    description: str,
    kind: NodeKind = "function",
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
            kind=kind,
            label=label,
            description=description,
            builtin=True,
        )
        # 1. 自动注册到全局注册表
        REGISTRY[node_name] = node_type
        # 2. 元数据绑定到函数对象本身，便于从函数侧反查
        setattr(func, "__node_type__", node_type)
        return func

    return decorator


def register(node_type: NodeType) -> None:
    """注册（或覆盖）一个节点类型，供 YAML 的 ``type:``/``condition:`` 引用。"""
    REGISTRY[node_type.name] = node_type


def unregister(name: str) -> None:
    """撤销 register() 的注册；系统内置类型（@node 注册）不可撤销。"""
    node_type = REGISTRY.get(name)
    if node_type is not None and not node_type.builtin:
        REGISTRY.pop(name, None)
