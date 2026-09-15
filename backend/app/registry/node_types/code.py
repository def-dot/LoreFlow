import logging
from typing import Any

from app.registry.node_type import node_type
from app.utils.http import http_client
from app.core.config import settings

logger = logging.getLogger(__name__)


@node_type(
    label="代码执行",
    description="执行 Python 脚本；指定 func 时按函数签名自动映射输入，返回值即输出",
    metadata={"group": "基础", "order": 20},
    input_schema=None,
    output_schema={"type": "any", "description": "脚本的返回值"},
)
async def code(ctx: dict[str, Any]) -> Any:
    script = ctx.get("script")
    if not script or not isinstance(script, str):
        raise ValueError("code 节点缺少 script")

    safe_ctx = {k: v for k, v in ctx.items() if not k.startswith("_")}
    resp = await http_client().post(
        f"{settings.SANDBOX_URL}/exec",
        json={"code": script, "ctx": safe_ctx},
        timeout=60,
    )
    result = resp.json()
    if resp.status_code != 200:
        raise RuntimeError(result["error"])
    return result["result"]
