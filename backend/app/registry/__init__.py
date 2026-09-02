"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

from .core import REGISTRY, NodeType, node_type, unregister
from . import basic, human, llm, rag, web  # noqa: F401  # 导入即触发 @node_type 注册

__all__ = [
    "NodeType",
    "REGISTRY",
    "node_type",
    "unregister",
]
