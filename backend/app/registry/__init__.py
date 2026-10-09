"""
统一注册表，提供 FuncDef 数据类和两个顶层 Registry。

设计原则
--------
1. FuncDef 数据类（types.py）：
   - name, func, label, description, metadata, input_schema, output_schema

2. 两个顶层 Registry（均为 dict[str, FuncDef]）：
   - REGISTRY      — 节点类型（DAG 引擎 / 节点扫描）
   - TOOL_REGISTRY — 工具定义（LLM Agent 工具列表）

3. 装饰器（types.py）：
   - @node()         注册到 REGISTRY（DAG 节点）
   - @tool()         注册到 TOOL_REGISTRY（Agent 工具）
   - @node_and_tool() 同时注册两侧

4. 注册/查询均为 O(1) dict 操作，模块级暴露，零构造开销。
"""

from .types import (
    REGISTRY,
    TOOL_REGISTRY,
    FuncDef,
    node,
    node_and_tool,
    tool,
)

# 导入内置节点类型和工具 — 触发装饰器注册
from . import funcs  # noqa: E402, F401

__all__ = [
    "FuncDef",
    "REGISTRY",
    "TOOL_REGISTRY",
    "node",
    "node_and_tool",
    "tool",
]