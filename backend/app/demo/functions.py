"""
Function implementations for pipeline.yaml.

The orchestration lives in pipeline.yaml; these are the pieces of actual
work it references. Shared by examples.py (demo_yaml_config) and the web
app (app/main.py), so both frontends run the exact same pipeline.
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


#: Registry passed to load_dag("pipeline.yaml", functions=FUNCTIONS)
FUNCTIONS = {
    "cfg_fetch": cfg_fetch,
    "cfg_clean": cfg_clean,
    "cfg_enrich": cfg_enrich,
    "cfg_merge": cfg_merge,
    "cfg_publish": cfg_publish,
    "cfg_needs_report": cfg_needs_report,
    "cfg_report": cfg_report,
}
