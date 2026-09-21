"""start — 工作流输入节点。

声明输入参数（InputParamDef），执行时透传外部输入供下游 $ 引用。
"""

from __future__ import annotations

from typing import Any

from ..types import func


@func(node=True, tool=False, label="输入")
async def start(**kwargs: Any):
    """透传外部输入，供下游节点通过 $ 引用取值。"""
    return kwargs
