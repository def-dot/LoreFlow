
from pydantic import BaseModel, Field

from app.registry.types import func


class StringResultOutput(BaseModel):
    result: str = Field(description="执行结果")


class PublishParams(BaseModel):
    title: str | None = Field(default=None, description="发布标题")
    content: str | None = Field(default=None, description="发布内容")


@func(
    label="内容发布",
    description="发布内容到小红书、抖音等社交平台",
    metadata={"group": "其他", "order": 10},

)
async def publish(params: PublishParams) -> StringResultOutput:
    return StringResultOutput(result=f"Published: {params.title or '(untitled)'}")


_svc_calls = 0


@func(
    label="调用外部API",
    description="外部 API 偶发超时",
    metadata={"group": "其他", "order": 20},
)
async def svc_external_api() -> StringResultOutput:
    global _svc_calls
    _svc_calls += 1
    if _svc_calls < 3:
        raise TimeoutError(f"外部 API 超时 — 第 {_svc_calls} 次调用")
    _svc_calls = 0
    return StringResultOutput(result="外部 API 调用成功（第 3 次尝试）")


@func(
    label="外部服务不可用",
    description="外部 API 持续故障",
    metadata={"group": "其他", "order": 30},
)
async def svc_unavailable() -> StringResultOutput:
    raise TimeoutError("外部 API 宕机 — 服务持续不可用")
