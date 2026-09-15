from __future__ import annotations

from app.registry.types import func


@func(
    label="内容发布",
    description="发布内容到小红书、抖音等社交平台",
    metadata={"group": "其他", "order": 10},
    params={"title": "发布标题", "content": "发布内容"},
    output_schema={"type": "string", "description": "发布结果"},
)
async def publish(title: str | None = None, content: str | None = None) -> str:
    return f"Published: {title or '(untitled)'}"


_svc_calls = 0


@func(
    label="调用外部API",
    description="外部 API 偶发超时",
    metadata={"group": "其他", "order": 20},
    output_schema={"type": "string", "description": "调用结果"},
)
async def svc_external_api() -> str:
    global _svc_calls
    _svc_calls += 1
    if _svc_calls < 3:
        raise TimeoutError(f"外部 API 超时 — 第 {_svc_calls} 次调用")
    _svc_calls = 0
    return "外部 API 调用成功（第 3 次尝试）"


@func(
    label="外部服务不可用",
    description="外部 API 持续故障",
    metadata={"group": "其他", "order": 30},
    output_schema={"type": "string", "description": "调用结果"},
)
async def svc_unavailable() -> str:
    raise TimeoutError("外部 API 宕机 — 服务持续不可用")
