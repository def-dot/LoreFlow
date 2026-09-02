from __future__ import annotations

from typing import Any

from app.registry.core import node_type


@node_type(label="发布", description="发布内容")
async def cfg_publish(ctx: dict[str, Any]) -> str:
    return f"Published: {ctx.get('title') or '(untitled)'}"


_svc_calls = 0


@node_type(label="调用外部API", description="外部 API 偶发超时")
async def svc_external_api(ctx: dict[str, Any]) -> str:
    global _svc_calls
    _svc_calls += 1
    if _svc_calls < 3:
        raise TimeoutError(f"外部 API 超时 — 第 {_svc_calls} 次调用")
    _svc_calls = 0
    return "外部 API 调用成功（第 3 次尝试）"


@node_type(label="外部服务不可用", description="外部 API 持续故障")
async def svc_unavailable(ctx: dict[str, Any]) -> str:
    raise TimeoutError("模拟外部 API 宕机 — 服务持续不可用")
