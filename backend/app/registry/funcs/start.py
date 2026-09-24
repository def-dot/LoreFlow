"""start — 工作流输入节点。

声明输入参数（InputParamDef），执行时透传外部输入供下游 $ 引用。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..types import func


class StartParams(BaseModel):
    model_config = ConfigDict(extra="allow")


@func(node=True, tool=False, label="输入")
async def start(params: StartParams) -> StartParams:
    """透传外部输入，供下游节点通过 $ 引用取值。"""
    return params
