"""
节点函数实现 — 注册表（app.registry）里的实际工作逻辑。

编排结构在 YAML（如 app/pipelines/pipeline.yaml）声明；这些函数是
YAML 里 type:/condition: 引用的实现。
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
    """取最近一次人工审核的 payload（人工节点输出固定含 decision/payload），
    从中找出带 title 的内容输出（如 merge / fetch 的 {title, body}）；
    扁平输入（如 08 的 title/content 字符串键）没有嵌套 dict，兜底取
    payload 顶层的 "title" 键。扫描前跳过 "_" 前缀的引擎保留键
    （声明视图的 _review 标签字典，否则会被误认成带 title 的内容）。
    """
    reviews = [v for v in ctx.values() if isinstance(v, dict) and "decision" in v and "payload" in v]
    payload = reviews[-1]["payload"] if reviews else ctx
    if isinstance(payload, dict):
        payload = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    titled = next(
        (v for v in reversed(list(payload.values())) if isinstance(v, dict) and "title" in v),
        {},
    )
    title = titled.get("title") or payload.get("title")
    return f"Published: {title or '(untitled)'}"


@node(kind="condition", label="是否需要报告", description="条件谓词：merge 有输出才执行下游")
def cfg_needs_report(ctx: dict[str, Any]) -> bool:
    return bool(ctx.get("merge"))


@node(label="生成报告", description="基于 merge 输出生成报告文本")
async def cfg_report(ctx: dict[str, Any]) -> str:
    return f"Report generated for {ctx['merge']['title']}"


# ---- 演示辅助 — app/pipelines 下各特性示例引用的函数 ----


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


@node(kind="condition", label="演示循环条件", description="循环谓词：iteration < 3 继续（多一个 iteration 参数）")
def demo_keep_iterating(ctx: dict[str, Any], iteration: int) -> bool:
    """循环条件：iteration < 3 时继续（loop 谓词多一个 iteration 参数）。"""
    return iteration < 3


@node(kind="condition", label="演示按需审核", description="条件谓词：正文超过 30 字符才需要人工审核")
def demo_needs_review(ctx: dict[str, Any]) -> bool:
    """条件谓词：合并正文超过 30 字符才需要人工审核。

    演示数据正文只有 26 字符 → 审核被跳过；调低阈值即可触发审核。
    """
    merge = ctx.get("merge")
    return bool(merge) and len(str(merge.get("body", ""))) > 30


@node(label="按查询检索", description="读取必填输入 query 与可选输入 topic，演示运行时参数校验")
async def demo_search(ctx: dict[str, Any]) -> dict[str, Any]:
    """演示必填/可选输入：query 由运行时提供（09 示例声明为必填），
    topic 有 YAML 默认值、可被运行时覆盖。"""
    await asyncio.sleep(0.05)
    return {"query": ctx["query"], "topic": ctx.get("topic", "")}
