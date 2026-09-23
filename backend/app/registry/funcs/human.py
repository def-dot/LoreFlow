import logging
from typing import Any

from pydantic import BaseModel, Field

from app.engine.types import SuspendExecution
from app.registry.types import func


class PayloadItem(BaseModel):
    key: str = Field(description="字段标识（英文）")
    label: str = Field(description="显示名称（中文）")
    value: Any = Field(description="字段值")


class HumanParams(BaseModel):
    payload: list[PayloadItem] = Field(default_factory=list, description="审核参数")

class HumanOutput(BaseModel):
    """审核节点输出：决策 + 审核参数最终值。"""

    approve: bool = Field(description="是否通过")
    reason: str = Field(default="", description="拒绝原因")
    result: dict[str, Any] = Field(default_factory=dict, description="审核结果")


logger = logging.getLogger(__name__)


@func(
    tool=False,
    label="人工审核",
    description="人工审核节点，暂停等待审批",
    metadata={"group": "基础", "order": 10},
)
async def human(params: HumanParams) -> HumanOutput:
    """挂起等待人工审批。决策由 approve 端点直接写入节点快照。"""
    payload_dict = {item.key: item.value for item in params.payload}
    payload_display = {item.key: item.label for item in params.payload}

    raise SuspendExecution(
        "等待人工审批",
        {"payload": payload_dict, "labels": payload_display},
    )
