from typing import Any

from app.registry.core import node

def _intent_cond(expected: str, branch: str) -> None:
    """按期望意图注册条件谓词 —— 一个实现、多个注册（逻辑同、比较值不同）。"""
    @node(name=f"is_{expected}",
          label=f"是{branch}", description=f"意图为 {expected}：{branch}支路执行")
    def _cond(ctx: dict[str, Any]) -> bool:
        classify = ctx.get("classify")
        return isinstance(classify, dict) and classify.get("intent") == expected


_intent_cond("simple", "简单问答")