"""
Function implementations for pipeline.yaml.

The orchestration lives in pipeline.yaml; these are the pieces of actual
work it references. Shared by examples.py (demo_yaml_config) and the web
app (app/main.py), so both frontends run the exact same pipeline.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict


async def cfg_fetch(ctx: Dict[str, Any]) -> dict:
    await asyncio.sleep(0.05)
    return {"title": "DAG Flow v0.1", "body": "  declarative config rocks  "}


async def cfg_clean(ctx: Dict[str, Any]) -> str:
    return ctx["fetch"]["body"].strip()


async def cfg_enrich(ctx: Dict[str, Any]) -> str:
    await asyncio.sleep(0.05)
    return f"[ENRICHED] {ctx['fetch']['title']}"


async def cfg_merge(ctx: Dict[str, Any]) -> dict:
    return {"title": ctx["enrich"], "body": ctx["clean"]}


async def cfg_publish(ctx: Dict[str, Any]) -> str:
    reviewed = ctx["review"]
    return f"Published: {reviewed['payload']['merge']['title']}"


def cfg_needs_report(ctx: Dict[str, Any]) -> bool:
    return bool(ctx.get("merge"))


async def cfg_report(ctx: Dict[str, Any]) -> str:
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
