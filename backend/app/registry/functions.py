"""
节点函数实现 — 注册表（app.registry）里的实际工作逻辑。

编排结构在 YAML（如 app/demo/pipeline.yaml）声明；这些函数是
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
