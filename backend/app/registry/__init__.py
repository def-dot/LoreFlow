"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

from .core import REGISTRY, NodeType, node, unregister
from . import demo, human, llm, rag, web  # noqa: F401  # 导入即触发 @node 注册

__all__ = [
    "NodeType",
    "REGISTRY",
    "node",
    "unregister",
]
