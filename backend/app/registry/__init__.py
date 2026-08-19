"""
结构化函数注册表 — 系统支持的节点类型目录。

YAML 的 ``type:`` / ``condition:`` 只能引用这里注册的名字。每个条目带元数据（kind/label/description），
供 API 枚举与未来页面配置使用；:func:`as_functions` 投影成引擎消费的
name → callable 映射。

human 节点（``kind: human``）是引擎级内置，不经过注册表。

运行期可通过 :func:`register` 追加类型（测试、插件扩展）；
:func:`resolve_function` 是引擎解析 ``type:``/``condition:`` 的入口。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .functions import (
    cfg_clean,
    cfg_enrich,
    cfg_fetch,
    cfg_merge,
    cfg_needs_report,
    cfg_publish,
    cfg_report,
    demo_flaky,
    demo_keep_iterating,
    demo_needs_review,
    demo_tick,
)

NodeKind = Literal["function", "condition"]


@dataclass(frozen=True)
class NodeType:
    """一个可被 YAML 引用的类型：节点函数或条件谓词。

    ``func`` 签名统一为 ``(ctx: dict) -> Any``；loop 的 condition
    谓词多一个 iteration 参数，由引擎调用侧保证。
    """

    name: str  #: YAML type:/condition: 引用的键
    func: Callable[..., Any]  #: 实现
    kind: NodeKind  #: function=节点函数, condition=条件谓词
    label: str  #: UI 显示名
    description: str  #: 给配置者看的说明


#: 系统节点类型目录
NODE_TYPES: tuple[NodeType, ...] = (
    NodeType(
        name="cfg_fetch",
        func=cfg_fetch,
        kind="function",
        label="抓取原始数据",
        description="拉取文章标题与正文，输出 {title, body}",
    ),
    NodeType(
        name="cfg_clean",
        func=cfg_clean,
        kind="function",
        label="清洗正文",
        description="去除 body 首尾空白",
    ),
    NodeType(
        name="cfg_enrich",
        func=cfg_enrich,
        kind="function",
        label="富化标题",
        description="给标题加 [ENRICHED] 前缀",
    ),
    NodeType(
        name="cfg_merge",
        func=cfg_merge,
        kind="function",
        label="合并字段",
        description="合并 enrich 与 clean 的输出为 {title, body}",
    ),
    NodeType(
        name="cfg_publish",
        func=cfg_publish,
        kind="function",
        label="发布",
        description="发布人工审核通过的合并结果",
    ),
    NodeType(
        name="cfg_report",
        func=cfg_report,
        kind="function",
        label="生成报告",
        description="基于 merge 输出生成报告文本",
    ),
    NodeType(
        name="cfg_needs_report",
        func=cfg_needs_report,
        kind="condition",
        label="是否需要报告",
        description="条件谓词：merge 有输出才执行下游",
    ),
    NodeType(
        name="demo_flaky",
        func=demo_flaky,
        kind="function",
        label="演示重试",
        description="前两次调用失败、第三次成功，演示 retry/backoff",
    ),
    NodeType(
        name="demo_tick",
        func=demo_tick,
        kind="function",
        label="演示循环计数",
        description="每轮迭代 tick+1，结果累积在共享上下文",
    ),
    NodeType(
        name="demo_keep_iterating",
        func=demo_keep_iterating,
        kind="condition",
        label="演示循环条件",
        description="循环谓词：iteration < 3 继续（多一个 iteration 参数）",
    ),
    NodeType(
        name="demo_needs_review",
        func=demo_needs_review,
        kind="condition",
        label="演示按需审核",
        description="条件谓词：正文超过 30 字符才需要人工审核",
    ),
)


#: 运行期注册表：系统内置类型 + register() 追加的动态类型
_REGISTRY: dict[str, NodeType] = {t.name: t for t in NODE_TYPES}

_BUILTIN_NAMES = frozenset(t.name for t in NODE_TYPES)


def register(node_type: NodeType) -> None:
    """注册（或覆盖）一个节点类型，供 YAML 的 ``type:``/``condition:`` 引用。"""
    _REGISTRY[node_type.name] = node_type


def unregister(name: str) -> None:
    """撤销 register() 的注册；系统内置类型不可撤销。"""
    if name not in _BUILTIN_NAMES:
        _REGISTRY.pop(name, None)


def all_types() -> tuple[NodeType, ...]:
    """全部可用节点类型（内置 + 动态注册）。"""
    return tuple(_REGISTRY.values())


def as_functions() -> dict[str, Callable[..., Any]]:
    return {name: t.func for name, t in _REGISTRY.items()}


def resolve_function(name: str) -> Callable[..., Any]:
    """按名字查找已注册的函数；未注册时给出明确报错。"""
    func = as_functions().get(name)
    if func is None:
        raise ValueError(f"节点 {name!r} 未注册")
    return func


__all__ = [
    "NodeType",
    "NODE_TYPES",
    "all_types",
    "as_functions",
    "register",
    "resolve_function",
    "unregister",
]
