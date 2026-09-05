"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

from .core import REGISTRY, NodeType, node_type, unregister
from .tools import TOOL_REGISTRY, tool
from . import base, llm, other, rag, web, tools  # noqa: F401  # 导入即触发注册

__all__ = [
    "NodeType",
    "REGISTRY",
    "TOOL_REGISTRY",
    "node_type",
    "tool",
    "unregister",
]
