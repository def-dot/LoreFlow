import logging
from typing import Any

from app.registry.types import func
from app.utils.http import http_client
from app.core.config import settings

logger = logging.getLogger(__name__)


@func(
    label="代码执行",
    description="执行 Python 脚本；指定 func 时按函数签名自动映射输入，返回值即输出",
    metadata={"group": "基础", "order": 20},
    params={"script": "要执行的 Python 脚本"},
    output_schema={"type": "any", "description": "脚本的返回值"},
)
async def code(script: str, **kwargs: Any) -> Any:
    if not script or not isinstance(script, str):
        raise ValueError("code 节点缺少 script")

    resp = await http_client().post(
        f"{settings.SANDBOX_URL}/exec",
        json={"code": script, "ctx": kwargs},
        timeout=60,
    )
    result = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(result["error"])
    return result["result"]
