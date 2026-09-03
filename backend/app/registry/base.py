import logging
import json
import re
from typing import Any

from app.registry.core import NodeGroup, node_type

logger = logging.getLogger(__name__)



@node_type(
    label="人工审核",
    description="人工审核节点，暂停等待审批",
    name="human",
    group=NodeGroup.BASE,
    input_schema={
        "_review": {"type": "object", "required": False, "description": "审核卡片声明 {$键: 标签文本}"},
        "_prompt": {"type": "string", "required": False, "description": "审核提示词（兼容旧版定义）"},
    },
    output_schema={
        "type": "object",
        "fields": {
            "payload": {"type": "object", "description": "审核载荷（提交给审核人的数据）"},
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
async def human_review(ctx: dict[str, Any]) -> dict[str, Any]:
    """审核协议：等待审批 → 通过输出决策 / 拒绝抛异常（级联跳过下游）。
    """
    approver = ctx.get("_approver")
    if approver is None:
        raise ValueError("人工审核节点缺少 approver —— load_dag(approver=...) 未提供")
    name = ctx["_node"]  # approver 按节点领取决策（make_approver 的 claim_decision）

    review = ctx.get("_review")
    if isinstance(review, dict) and review:
        # 卡片键带 $ 引用前缀（声明层约定）；载荷与决策字段用剥前缀后的裸键
        fields = {k.removeprefix("$"): ctx.get(k.removeprefix("$")) for k in review}
        payload: dict[str, Any] = {**fields, "_review": {k.removeprefix("$"): v for k, v in review.items()}}
    else:
        payload = {k: v for k, v in ctx.items() if not k.startswith("_")}

    logger.info(f"\n  [REVIEW] node {name!r} is waiting for human approval")

    decision = await approver(name, payload)
    if decision.get("approve"):
        logger.info("[%s] approved by human reviewer", name)
        return {"payload": payload, "decision": decision}

    from app.engine.node import HumanRejected

    reason = f"人工审核拒绝：{decision.get("reason")}"
    logger.warning("[%s] REJECTED by human reviewer: %s", name, reason)
    raise HumanRejected(reason, output={"payload": payload, "decision": decision})


@node_type(
    label="代码执行",
    description="执行 Python 脚本，脚本中可用 inputs 作为局部变量，须给 result 赋值作为输出",
    group=NodeGroup.BASE,
    input_schema={},
    output_schema={"type": "any", "description": "脚本中 result 变量的值"},
)
async def code(ctx: dict[str, Any]) -> Any:
    script = ctx.get("_script")
    if not script or not isinstance(script, str):
        raise ValueError("code 节点缺少 script")

    # 构建局部命名空间：inputs 作为变量 + 常用模块
    local_ns: dict[str, Any] = {
        k: v for k, v in ctx.items() if not k.startswith("_")
    }
    local_ns["json"] = json
    local_ns["re"] = re

    exec(script, {"__builtins__": __builtins__}, local_ns)  # noqa: S102

    if "result" not in local_ns:
        raise ValueError("code 脚本必须给 result 赋值")
    return local_ns["result"]
