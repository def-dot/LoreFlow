import logging
from typing import Any

from app.registry.types import func

logger = logging.getLogger(__name__)


@func(
    label="人工审核",
    description="人工审核节点，暂停等待审批",
    name="human",
    metadata={"group": "基础", "order": 10},
    params={"_review": "审核卡片声明 {$键: 标签文本}"},
    output_schema={
        "type": "object",
        "fields": {
            "payload": {"type": "object", "description": "审核载荷"},
            "decision": {
                "type": "object",
                "description": "审核决策",
                "fields": {
                    "approve": {"type": "boolean", "description": "是否通过"},
                    "reason": {"type": "string", "description": "拒绝原因（可选）"},
                },
            },
        },
    },
)
async def human_review(_approver: Any, _node: str, _review: dict | None = None, **kwargs: Any) -> dict[str, Any]:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。
    """
    if _approver is None:
        raise ValueError("人工审核节点缺少 approver —— dag.run(approver=...) 未提供")
    if isinstance(_review, dict) and _review:
        # 卡片键带 $ 引用前缀（声明层约定）；载荷与决策字段用剥前缀后的裸键
        fields = {k.removeprefix("$"): kwargs.get(k.removeprefix("$")) for k in _review}
        payload: dict[str, Any] = {**fields, "_review": {k.removeprefix("$"): v for k, v in _review.items()}}
    else:
        payload = dict(kwargs)

    logger.info(f"\n  [REVIEW] node {_node!r} is waiting for human approval")

    decision = await _approver(_node, payload)
    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", _node)
        return {"payload": payload, "decision": decision}

    from app.engine.node import HumanRejected

    reason = f"人工审核拒绝：{decision.get("reason")}"
    logger.warning("[%s] REJECTED by human reviewer: %s", _node, reason)
    raise HumanRejected(reason, output={"payload": payload, "decision": decision})
