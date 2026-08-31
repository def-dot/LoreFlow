"""
演示节点 — app/pipelines 下 YAML 示例引用的函数实现。

编排结构（依赖/接线/条件）在 YAML 声明；这些函数是 YAML 里 type:
引用的实现。cfg_* 是主链演示（抓取→清洗→富化→合并→发布→报告），
demo_* 是引擎特性示例（重试/循环）的辅助节点。条件一律用表达式
（``condition: merge`` / ``iteration < 3``），不再是注册函数。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.registry.core import node


@node(label="抓取原始数据", description="拉取文章标题与正文，输出 {title, body}")
async def cfg_fetch(ctx: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.05)
    return {"title": "DAG Flow v0.1", "body": "  declarative config rocks  "}


@node(label="清洗正文", description="去除 body 首尾空白")
async def cfg_clean(ctx: dict[str, Any]) -> str:
    return str(ctx["fetch"]["body"]).strip()


@node(label="富化标题", description="给标题加 [ENRICHED] 前缀")
async def cfg_enrich(ctx: dict[str, Any]) -> str:
    await asyncio.sleep(0.05)
    return f"[ENRICHED] {ctx['fetch']['title']}"


@node(label="合并字段", description="合并 enrich 与 clean 的输出为 {title, body}")
async def cfg_merge(ctx: dict[str, Any]) -> dict[str, Any]:
    return {"title": ctx["enrich"], "body": ctx["clean"]}


@node(label="发布", description="发布人工审核通过的内容")
async def cfg_publish(ctx: dict[str, Any]) -> str:
    """发布最后一级人工审核通过的内容。

    生效值 = 最后一级 approved 审核的载荷数据键，内联其决策里的平铺
    卡片字段（文本字段的修订，扣除协议键 approve/reason；引擎不写回
    共享上下文）。从中找出带 title 的内容输出（如 merge / fetch 的
    {title, body}），扁平输入（title/content 字符串键）直接取顶层
    "title" 键。无审核节点时兜底全 ctx。
    """
    reviews = [
        v
        for v in ctx.values()
        if isinstance(v, dict) and v.get("approved") and isinstance(v.get("payload"), dict)
    ]
    if reviews:
        last = reviews[-1]
        data = {k: v for k, v in last["payload"].items() if not str(k).startswith("_")}
        fields = {
            k: v
            for k, v in (last.get("decision") or {}).items()
            if k not in ("approve", "reason")
        }
        payload = {**data, **fields}
    else:
        payload = ctx
    titled = next(
        (v for v in reversed(list(payload.values())) if isinstance(v, dict) and "title" in v),
        {},
    )
    title = titled.get("title") or payload.get("title")
    return f"Published: {title or '(untitled)'}"


@node(label="生成报告", description="基于 merge 输出生成报告文本")
async def cfg_report(ctx: dict[str, Any]) -> str:
    return f"Report generated for {ctx['merge']['title']}"


# ---- 特性示例辅助 — 重试/循环示例引用的函数 ----


_flaky_calls = 0


@node(label="演示重试", description="前两次调用失败、第三次成功，演示 retry/backoff")
async def demo_flaky(ctx: dict[str, Any]) -> str:
    """前两次调用失败、第三次成功 — 演示 retry/backoff。

    失败计数是进程级全局，同一进程第二次运行会直接成功。
    """
    global _flaky_calls
    _flaky_calls += 1
    if _flaky_calls < 3:
        raise RuntimeError(f"临时故障 — 第 {_flaky_calls} 次调用")
    return "flaky succeeded after 2 failures"


@node(label="演示循环计数", description="每轮迭代 tick+1，结果累积在共享上下文")
async def demo_tick(ctx: dict[str, Any]) -> int:
    """循环体：每轮迭代把 tick 计数 +1（结果累积在共享上下文）。"""
    return int(ctx.get("tick", 0)) + 1
