"""start — 工作流输入节点。

虚拟入口，将 inputs 声明注入拓扑排序，本身不产出值。
"""

from __future__ import annotations

from typing import Any

from ..types import func


@func(node=True, tool=False, label="输入")
async def start(**kwargs: Any):
    """无操作 — inputs 在 DAG.run() 初始化 ctx 时已就位。"""
    return None
