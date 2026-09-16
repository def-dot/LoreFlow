import logging
from typing import Any

from pydantic import BaseModel, Field

from app.registry.types import func
from app.utils.http import http_client
from app.core.config import settings

logger = logging.getLogger(__name__)


class CodeOutput(BaseModel):
    result: Any = Field(description="脚本返回值")


class CodeParams(BaseModel):
    script: str = Field(description="要执行的 Python 脚本")


@func(
    label="代码执行",
    description="执行 Python 脚本；指定 func 时按函数签名自动映射输入，返回值即输出",
    metadata={"group": "基础", "order": 20},

)
async def code(params: CodeParams, **kwargs: Any) -> CodeOutput:
    resp = await http_client().post(
        f"{settings.SANDBOX_URL}/exec",
        json={"code": params.script, "params": kwargs},
        timeout=60,
    )
    result = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(result["error"])
    return CodeOutput(result=result["result"])
