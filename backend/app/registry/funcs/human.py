import logging
from typing import Any

from pydantic import BaseModel, Field

from app.engine.types import HumanRejected, NodeContext, SuspendExecution, current_node_ctx
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
    """审核协议：有已存储决策则处理，否则挂起等待审批。"""
    ctx: NodeContext = current_node_ctx.get()

    # 展平 payload 列表为 dict
    payload_dict = {item.key: item.value for item in params.payload}
    payload_display = {item.key: item.label for item in params.payload}

    # 恢复场景：有已存储的决策
    if ctx.stored_decision:
        decision = ctx.stored_decision
        edits = decision.get("edits", {})
        final = {**payload_dict, **edits}

        if decision.get("approve"):
            logger.info("[%s] approved by human reviewer", ctx.node_name)
            return HumanOutput(approve=True, reason=decision.get("reason", ""), result=final)

        reason = f"人工审核拒绝：{decision.get('reason')}"
        logger.warning("[%s] REJECTED by human reviewer: %s", ctx.node_name, reason)
        raise HumanRejected(reason, output=HumanOutput(approve=False, reason=decision.get("reason", ""), result=final))

    # 首次运行：挂起
    logger.info("[REVIEW] node %r is waiting for human approval", ctx.node_name)
    raise SuspendExecution(
        f"节点 {ctx.node_name} 等待人工审批",
        {"payload": payload_dict, "labels": payload_display},
    )
