from __future__ import annotations

from typing import Any
import re
import json

from app.registry.core import NodeGroup, node_type


@node_type(
    label="内容发布",
    description="发布内容到小红书、抖音等社交平台",
    group=NodeGroup.OTHER,
    input_schema={
        "title": {"type": "string", "required": False, "description": "发布标题"},
        "content": {"type": "string", "required": False, "description": "发布内容"},
    },
    output_schema={"type": "string", "description": ""},
)
async def cfg_publish(ctx: dict[str, Any]) -> str:
    return f"Published: {ctx.get('title') or '(untitled)'}"


_svc_calls = 0


@node_type(
    label="调用外部API",
    description="外部 API 偶发超时",
    group=NodeGroup.OTHER,
    input_schema={},
    output_schema={"type": "string", "description": ""},
)
async def svc_external_api(ctx: dict[str, Any]) -> str:
    global _svc_calls
    _svc_calls += 1
    if _svc_calls < 3:
        raise TimeoutError(f"外部 API 超时 — 第 {_svc_calls} 次调用")
    _svc_calls = 0
    return "外部 API 调用成功（第 3 次尝试）"


@node_type(
    label="外部服务不可用",
    description="外部 API 持续故障",
    group=NodeGroup.OTHER,
    input_schema={},
    output_schema={"type": "string", "description": ""},
)
async def svc_unavailable(ctx: dict[str, Any]) -> str:
    raise TimeoutError("模拟外部 API 宕机 — 服务持续不可用")


@node_type(
    label="代码执行",
    description="执行 Python 脚本，脚本中可用 inputs 作为局部变量，须给 result 赋值作为输出",
    group=NodeGroup.OTHER,
    input_schema={},
    output_schema={"type": "any", "description": "脚本中 result 变量的值"},
)
async def code(ctx: dict[str, Any]) -> Any:
    script = ctx.get("_script")
    if not script or not isinstance(script, str):
        raise ValueError("code 节点缺少 script")

    # 构建局部命名空间：inputs 作为变量 + 常用模块
    local_ns: dict[str, Any] = {
        k: v for k, v in ctx.items() if not k.startswith("_")
    }
    local_ns["json"] = json
    local_ns["re"] = re

    exec(script, {"__builtins__": __builtins__}, local_ns)  # noqa: S102

    if "result" not in local_ns:
        raise ValueError("code 脚本必须给 result 赋值")
    return local_ns["result"]
