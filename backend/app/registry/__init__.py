"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

# 导入顺序 = 注册顺序 = /node-types 目录顺序（REGISTRY 按插入序迭代）
from . import llm, demo, rag, web  # noqa: F401  # 导入即触发 @node 注册
from .core import REGISTRY, NodeKind, NodeType, node, unregister

__all__ = [
    "NodeKind",
    "NodeType",
    "REGISTRY",
    "node",
    "unregister",
]
