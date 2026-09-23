"""end — 工作流输出节点。

从接线视图中提取声明的键，返回值即为工作流输出。
"""

from pydantic import BaseModel, ConfigDict

from ..types import func


class EndParams(BaseModel):
    model_config = ConfigDict(extra="allow")


@func(node=True, tool=False, label="输出")
async def end(params: EndParams):
    """返回 params 中所有键。"""
    return params.model_dump()
