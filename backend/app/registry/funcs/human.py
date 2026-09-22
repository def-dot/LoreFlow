import logging
from typing import Any

from pydantic import BaseModel, Field

from app.engine.types import NodeContext
from app.registry.types import func


class HumanReviewInput(BaseModel):
    payload: dict[str, Any] = Field(default_factory=dict, description="审核参数")

class HumanReviewOutput(BaseModel):
    """审核节点输出：决策 + 审核参数最终值。"""

    approve: bool = Field(description="是否通过")
    reason: str = Field(default="", description="拒绝原因")
    result: dict[str, Any] = Field(default_factory=dict, description="审核参数最终值")


logger = logging.getLogger(__name__)


@func(
    tool=False,
    label="人工审核",
    description="人工审核节点，暂停等待审批",
    metadata={"group": "基础", "order": 10},
)
async def human(
    ctx: NodeContext,
    params: HumanReviewInput,
) -> HumanReviewOutput:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。

    输入：inputs 所有字段均为审核内容，key 即显示名，value 即实际值。
    输出：HumanReviewOutput（approve + reason + result）

    approver 返回格式：
    - {"approve": True}                                — 通过
    - {"approve": True, "edits": {"标题": "修改后"}}     — 通过，带修订
    - {"approve": False, "reason": "原因"}               — 拒绝
    """
    if ctx.approver is None:
        raise ValueError("人工审核节点缺少 approver —— dag.run(approver=...) 未提供")

    logger.info(f"\n  [REVIEW] node {ctx.node_name!r} is waiting for human approval")

    decision = await ctx.approver(ctx.node_name, params.payload)
    edits = decision.get("edits", {})
    final = {**params.payload, **edits}

    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", ctx.node_name)
        return HumanReviewOutput(approve=True, reason=decision.get("reason", ""), result=final)

    from app.engine.types import HumanRejected

    reason = f"人工审核拒绝：{decision.get('reason')}"
    logger.warning("[%s] REJECTED by human reviewer: %s", ctx.node_name, reason)
    raise HumanRejected(reason, output=HumanReviewOutput(approve=False, reason=decision.get("reason", ""), result=final))