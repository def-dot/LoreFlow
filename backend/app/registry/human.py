"""
human 人工审核类型 — 审核协议函数（静态节点函数）与注册。

与普通注册函数同签名 ``(ctx) -> 输出``，唯一差异是审核结果从当前
运行获取：``_approver`` 由 ``DAG.run`` 注入共享上下文（决策挂起/领取
见 ``orchestrator.make_approver``）。载荷即卡片（``_review`` 声明），
通过后 decision 携带审核者返回的字段终值 ``values``——文本字段经
``$节点.decision.values.键`` 引用，非文本字段不可编辑、经
``$节点.payload.键`` 取原值（不写回共享上下文）。

本模块是叶子模块（除 registry.core 外只依赖标准库）：engine 反向依赖
registry，协议放这里才不会成环；``HumanRejected`` 在拒绝分支运行时
导入，同因。
"""

import logging
from typing import Any

from app.registry.core import node_type

logger = logging.getLogger(__name__)


@node_type(label="人工审核", description="人工审核节点，暂停等待审批", name="human")
async def human_review(ctx: dict[str, Any]) -> dict[str, Any]:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。
    """
    approver = ctx.get("_approver")
    if approver is None:
        raise ValueError("人工审核节点缺少 approver —— load_dag(approver=...) 未提供")
    name = ctx["_node"]  # approver 按节点领取决策（make_approver 的 claim_decision）

    review = ctx.get("_review")
    if isinstance(review, dict) and review:
        payload: dict[str, Any] = {k: ctx.get(k) for k in review}
        payload["_review"] = review
    else:
        payload = {k: v for k, v in ctx.items() if not k.startswith("_")}
    if isinstance(ctx.get("_prompt"), str):
        payload["_prompt"] = ctx["_prompt"]

    logger.info(f"\n  [REVIEW] node {name!r} is waiting for human approval")

    decision = await approver(name, payload)
    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", name)
        return {"payload": payload, "decision": decision}

    from app.engine.node import HumanRejected

    reason = f"人工审核拒绝：{decision.get("reason")}"
    logger.warning("[%s] REJECTED by human reviewer: %s", name, reason)
    raise HumanRejected(reason, output={"payload": payload, "decision": decision})
