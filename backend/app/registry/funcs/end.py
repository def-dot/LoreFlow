"""end — 工作流输出节点。

从接线视图中提取声明的键，返回值即为工作流输出。
"""

from ..types import func


@func(node=True, tool=False, label="输出")
async def end(**kwargs):
    """返回 kwargs 中所有键。"""
    return kwargs
