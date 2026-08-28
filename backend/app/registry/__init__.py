"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

from . import demo, llm, rag, web  # noqa: F401  # 导入即触发 @node 注册
from .core import REGISTRY, NodeType, node, unregister

__all__ = [
    "NodeType",
    "REGISTRY",
    "node",
    "unregister",
]
