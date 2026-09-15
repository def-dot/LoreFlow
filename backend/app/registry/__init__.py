"""
app.registry
~~~~~~~~~~~~
统一注册表，提供 FuncDef 数据类和两个顶层 Registry。

设计原则
--------
1. FuncDef 数据类（types.py）：
   - name, func, label, description, metadata, input_schema, output_schema
   - frozen=True，可安全作为 dict key 或 set 元素

2. 两个顶层 Registry（均为 dict[str, FuncDef]）：
   - REGISTRY      — 节点类型（DAG 引擎 / 节点扫描）
   - TOOL_REGISTRY — 工具定义（LLM Agent 工具列表）

3. @func() 装饰器（types.py）：
   - node=True  注册到 REGISTRY
   - tool=True  注册到 TOOL_REGISTRY

4. 注册/查询均为 O(1) dict 操作，模块级暴露，零构造开销。
"""

from .types import (
    REGISTRY,
    TOOL_REGISTRY,
    FuncDef,
    execute_tool_call,
    func,
    input_schema_to_openai,
    unregister,
    unregister_tool,
)

# 导入内置节点类型和工具 — 触发 @func 装饰器注册
from . import funcs  # noqa: E402, F401

__all__ = [
    # 统一函数定义
    "FuncDef",
    # 双注册表
    "REGISTRY",
    "TOOL_REGISTRY",
    # 装饰器
    "func",
    # 注销
    "unregister",
    "unregister_tool",
    # 工具执行 & 格式转换
    "execute_tool_call",
    "input_schema_to_openai",
]