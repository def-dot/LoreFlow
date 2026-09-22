import logging
from typing import Any

from pydantic import BaseModel, Field

from app.engine.types import NodeContext
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
async def human(
    ctx: NodeContext,
    params: HumanParams,
) -> HumanOutput:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。

    输入：payload 为列表，每项含 key（英文标识）、label（中文显示名）、value（字段值）。
    输出：HumanReviewOutput（approve + reason + result）

    approver 返回格式：
    - {"approve": True}                                — 通过
    - {"approve": True, "edits": {"title": "修改后"}}   — 通过，带修订
    - {"approve": False, "reason": "原因"}               — 拒绝
    """
    if ctx.approver is None:
        raise ValueError("人工审核节点缺少 approver —— dag.run(approver=...) 未提供")

    logger.info(f"\n  [REVIEW] node {ctx.node_name!r} is waiting for human approval")

    # 展平 payload 列表为 dict
    payload_dict = {item.key: item.value for item in params.payload}
    payload_display = {item.key: item.label for item in params.payload}

    decision = await ctx.approver(ctx.node_name, payload_dict, payload_display)
    edits = decision.get("edits", {})
    final = {**payload_dict, **edits}

    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", ctx.node_name)
        return HumanOutput(approve=True, reason=decision.get("reason", ""), result=final)

    from app.engine.types import HumanRejected

    reason = f"人工审核拒绝：{decision.get('reason')}"
    logger.warning("[%s] REJECTED by human reviewer: %s", ctx.node_name, reason)
    raise HumanRejected(reason, output=HumanOutput(approve=False, reason=decision.get("reason", ""), result=final))
