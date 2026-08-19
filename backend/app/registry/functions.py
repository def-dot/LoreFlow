"""
节点函数实现 — 注册表（app.registry）里的实际工作逻辑。

编排结构在 YAML（如 app/pipelines/pipeline.yaml）声明；这些函数是
YAML 里 type:/condition: 引用的实现。
"""

from __future__ import annotations

import asyncio
from typing import Any


async def cfg_fetch(ctx: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.05)
    return {"title": "DAG Flow v0.1", "body": "  declarative config rocks  "}


async def cfg_clean(ctx: dict[str, Any]) -> str:
    return str(ctx["fetch"]["body"]).strip()


async def cfg_enrich(ctx: dict[str, Any]) -> str:
    await asyncio.sleep(0.05)
    return f"[ENRICHED] {ctx['fetch']['title']}"


async def cfg_merge(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"title": ctx["enrich"], "body": ctx["clean"]}


async def cfg_publish(ctx: dict[str, Any]) -> str:
    reviewed = ctx["review"]
    return f"Published: {reviewed['payload']['merge']['title']}"


def cfg_needs_report(ctx: dict[str, Any]) -> bool:
    return bool(ctx.get("merge"))


async def cfg_report(ctx: dict[str, Any]) -> str:
    return f"Report generated for {ctx['merge']['title']}"


# ---- 演示辅助 — app/pipelines 下各特性示例引用的函数 ----


_flaky_calls = 0


async def demo_flaky(ctx: dict[str, Any]) -> str:
    """前两次调用失败、第三次成功 — 演示 retry/backoff。

    失败计数是进程级全局，同一进程第二次运行会直接成功。
    """
    global _flaky_calls
    _flaky_calls += 1
    if _flaky_calls < 3:
        raise RuntimeError(f"临时故障 — 第 {_flaky_calls} 次调用")
    return "flaky succeeded after 2 failures"


async def demo_tick(ctx: dict[str, Any]) -> int:
    """循环体：每轮迭代把 tick 计数 +1（结果累积在共享上下文）。"""
    return int(ctx.get("tick", 0)) + 1


def demo_keep_iterating(ctx: dict[str, Any], iteration: int) -> bool:
    """循环条件：iteration < 3 时继续（loop 谓词多一个 iteration 参数）。"""
    return iteration < 3


def demo_needs_review(ctx: dict[str, Any]) -> bool:
    """条件谓词：合并正文超过 30 字符才需要人工审核。

    演示数据正文只有 26 字符 → 审核被跳过；调低阈值即可触发审核。
    """
    merge = ctx.get("merge")
    return bool(merge) and len(str(merge.get("body", ""))) > 30
