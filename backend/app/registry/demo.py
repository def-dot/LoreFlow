"""
演示节点 — app/pipelines 下 YAML 示例引用的函数实现。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.registry.core import node_type


@node_type(label="发布", description="发布人工审核通过的内容")
async def cfg_publish(ctx: dict[str, Any]) -> str:
    """发布审核生效值：title/content 由接线注入（``$审核节点.decision.键``）。"""
    return f"Published: {ctx.get('title') or '(untitled)'}"


_svc_calls = 0


@node_type(label="调用外部API", description="模拟外部 API 偶发超时：每轮前两次调用抛 TimeoutError、第三次成功，演示 retry/backoff")
async def svc_external_api(ctx: dict[str, Any]) -> str:
    """外部 API 偶发超时 — 每轮前两次抛 TimeoutError、第三次成功。
    """
    global _svc_calls
    _svc_calls += 1
    if _svc_calls < 3:
        raise TimeoutError(f"外部 API 超时 — 第 {_svc_calls} 次调用")
    _svc_calls = 0
    return "外部 API 调用成功（第 3 次尝试）"


@node_type(label="外部服务不可用", description="模拟外部 API 持续故障：每次调用都抛 TimeoutError（服务宕机），演示重试耗尽")
async def svc_unavailable(ctx: dict[str, Any]) -> str:
    """模拟外部 API 持续故障 — 每次调用都抛 TimeoutError。

    与 svc_external_api（前两次失败、第三次成功）对照：
    这个节点永远不会成功，重试多少次都会耗尽。
    """
    raise TimeoutError("模拟外部 API 宕机 — 服务持续不可用")
