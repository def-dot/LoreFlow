"""
结构化函数注册表 — 系统支持的节点类型目录。
"""

from . import demo, llm, rag, web  # noqa: F401  # 导入即触发 @node 注册
from .core import REGISTRY, NodeType, node, unregister


def _noop(_: dict) -> None:
    """占位函数 — 结构化类型（human/loop）的实际逻辑由 DAG 方法实现。"""


# 注册结构化类型：YAML 可统一用 type: 引用，kind 字段可省略
REGISTRY["human"] = NodeType(name="human", func=_noop, label="人工审核", description="人工审核节点，暂停等待审批")
REGISTRY["loop"] = NodeType(name="loop", func=_noop, label="循环", description="循环节点，重复执行 body 子流水线")

__all__ = [
    "NodeType",
    "REGISTRY",
    "node",
    "unregister",
]
