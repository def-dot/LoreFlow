import logging
from typing import Any

from pydantic import BaseModel, Field

from app.registry.types import func


class HumanDecision(BaseModel):
    approve: bool = Field(description="是否通过")
    reason: str = Field(default="", description="拒绝原因（可选）")


class HumanReviewOutput(BaseModel):
    payload: dict = Field(description="审核载荷")
    decision: HumanDecision = Field(description="审核决策")


class HumanReviewParams(BaseModel):
    review: dict[str, str] | None = Field(default=None, description="审核卡片声明 {$键: 标签文本}")


logger = logging.getLogger(__name__)


@func(
    tool=False,
    label="人工审核",
    description="人工审核节点，暂停等待审批",

    metadata={"group": "基础", "order": 10},

)
async def human(params: HumanReviewParams, _approver: Any = None, _node: str = "", **kwargs: Any) -> HumanReviewOutput:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。

    review 由引擎预解析 $ 引用，值即实际数据。
    _raw_review 为引擎自动注入的原始模板（含 $ 前缀键和标签文本）。
    """
    if _approver is None:
        raise ValueError("人工审核节点缺少 approver —— dag.run(approver=...) 未提供")
    if isinstance(params.review, dict) and params.review:
        raw: dict[str, str] = kwargs.pop("_raw_review", {})
        labels = {k.removeprefix("$"): v for k, v in raw.items()} if raw else {}
        payload: dict[str, Any] = {**params.review, "_review": labels or params.review}
    else:
        payload = dict(kwargs)

    logger.info(f"\n  [REVIEW] node {_node!r} is waiting for human approval")

    decision = await _approver(_node, payload)
    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", _node)
        return HumanReviewOutput(payload=payload, decision=HumanDecision(**decision))

    from app.engine.node import HumanRejected

    reason = f"人工审核拒绝：{decision.get("reason")}"
    logger.warning("[%s] REJECTED by human reviewer: %s", _node, reason)
    raise HumanRejected(reason, output={"payload": payload, "decision": decision})
