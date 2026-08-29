"""
human 人工审核类型 — 审核协议函数（静态节点函数）与注册。

与普通注册函数同签名 ``(ctx) -> 输出``，唯一差异是审核结果从当前
运行获取：``_approver`` 由 ``DAG.run`` 注入共享上下文（决策挂起/领取
见 ``orchestrator.make_approver``）。载荷 = 接线键（``_wired``，由
``_wired_func`` 注入；``_prompt``/``_review`` 等字面量保留键原样进
载荷），未接线则为整个视图去掉自身与 ``_`` 保留键。通过后的 decision
``edits`` 由 executor 统一回放共享上下文（与 resume 恢复同一条路径）。

本模块是叶子模块（除 registry.core 外只依赖标准库）：engine 反向依赖
registry，协议放这里才不会成环；``HumanRejected`` 在拒绝分支运行时
导入，同因。
"""

import logging
from datetime import datetime
from typing import Any

from app.registry.core import REGISTRY, NodeType

logger = logging.getLogger(__name__)


async def human_review(ctx: dict[str, Any]) -> dict[str, Any]:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。

    载荷即卡片：声明 ``_review``（inputs 字面量 ``{key: {label}}``）时
    只含声明键（声明即卡片规格），未声明则为整个视图去掉 ``_`` 保留键；
    ``_prompt`` 声明则置顶携带。
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

    print(f"\n  [REVIEW] node {name!r} is waiting for human approval")
    if isinstance(payload.get("_prompt"), str):
        print(f"  {payload['_prompt']}")

    decision = await approver(name, payload)
    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", name)
        return {
            "approved": True,
            "payload": payload,
            "decision": decision,
            "approved_at": datetime.now().isoformat(timespec="seconds"),
        }

    # engine.node 反向依赖 registry，运行时导入断开初始化环（拒绝是冷路径）
    from app.engine.node import HumanRejected

    reason = decision.get("reason") or "被人工审核拒绝"
    logger.warning("[%s] REJECTED by human reviewer: %s", name, reason)
    raise HumanRejected(
        reason,
        output={
            "approved": False,
            "reason": reason,
            "payload": payload,
            "decision": decision,
            "approved_at": datetime.now().isoformat(timespec="seconds"),
        },
    )


REGISTRY["human"] = NodeType(
    name="human",
    func=human_review,
    label="人工审核",
    description="人工审核节点，暂停等待审批",
)
