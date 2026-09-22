import logging
from typing import Any

from pydantic import BaseModel, Field

from app.registry.types import func


class ReviewField(BaseModel):
    """审核卡片中单个展示字段的声明。"""

    label: str = Field(description="显示标签")
    description: str | None = Field(default=None, description="补充说明（可选）")


class ReviewCard(BaseModel):
    """审核卡片：所有待审核字段的集合（动态 key）。"""

    model_config = {"extra": "allow"}


class HumanDecision(BaseModel):
    approve: bool = Field(description="是否通过")
    reason: str = Field(default="", description="拒绝原因（可选）")


class HumanReviewOutput(BaseModel):
    payload: dict = Field(description="审核载荷")
    decision: HumanDecision = Field(description="审核决策")


logger = logging.getLogger(__name__)


@func(
    tool=False,
    label="人工审核",
    description="人工审核节点，暂停等待审批",
    metadata={"group": "基础", "order": 10},
)
async def human(
    _approver: Any = None,
    _node: str = "",
    _review: ReviewCard | None = None,
    **kwargs: Any,
) -> HumanReviewOutput:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。

    inputs 结构约定：
    - _review: 审核卡片（ReviewField 声明，value 由引擎解析 $引用）
    - 其余 key: 实际审核内容（引擎已解析 $引用 为真实值）
    """
    if _approver is None:
        raise ValueError("人工审核节点缺少 approver —— dag.run(approver=...) 未提供")

    if _review is not None:
        payload = {"review": dict(_review.model_extra)}
    else:
        payload = {k: v for k, v in kwargs.items() if not k.startswith("_")}

    logger.info(f"\n  [REVIEW] node {_node!r} is waiting for human approval")

    decision = await _approver(_node, payload)
    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", _node)
        return HumanReviewOutput(payload=payload, decision=HumanDecision(**decision))

    from app.engine.types import HumanRejected

    reason = f"人工审核拒绝：{decision.get('reason')}"
    logger.warning("[%s] REJECTED by human reviewer: %s", _node, reason)
    raise HumanRejected(reason, output={"payload": payload, "decision": decision})