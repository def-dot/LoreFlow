import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.engine.types import SuspendExecution
from app.registry.types import func


class HumanInput(BaseModel):
    """审核节点输入：字段由 pipeline YAML 动态声明，允许任意键。"""
    model_config = ConfigDict(extra="allow")


class HumanOutput(BaseModel):
    """审核节点输出：决策 + 审核参数最终值。"""
    model_config = ConfigDict(extra="allow")

    approve: bool = Field(description="是否通过")
    reason: str = Field(default="", description="拒绝原因")


logger = logging.getLogger(__name__)


@func(
    tool=False,
    label="人工审核",
    description="人工审核节点，暂停等待审批",
    metadata={"group": "基础", "order": 10},
)
async def human(params: HumanInput) -> HumanOutput:
    """挂起等待人工审批。决策由 approve 端点直接写入节点快照。"""
    raise SuspendExecution("等待人工审批")
