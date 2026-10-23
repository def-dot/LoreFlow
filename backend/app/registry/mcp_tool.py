"""MCP 工具注册 — 从 mcp-service 拉列表，注册为 HTTP 代理 FuncDef。"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.registry.types import TOOL_REGISTRY, FuncDef
from app.schemas.mcp import McpServerState
from app.utils.http import http_client

logger = logging.getLogger(__name__)


def _make_mcp_call(tool_name: str):
    """构造代理函数：调用 mcp-service 的 /tools/{name}/call。"""
    async def _call(**kwargs: Any) -> str:
        resp = await http_client().post(
            f"{settings.MCP_SERVICE_URL}/tools/{tool_name}/call",
            json={"args": kwargs},
        )
        resp.raise_for_status()
        return resp.json()["result"]
    return _call


def register_mcp_tools(servers: list[McpServerState]) -> None:
    """注册 mcp-service 工具到 TOOL_REGISTRY（先清旧的 MCP 条目）。"""
    stale = [k for k, v in TOOL_REGISTRY.items() if v.metadata.get("source") == "mcp"]
    for k in stale:
        TOOL_REGISTRY.pop(k, None)

    count = 0
    for s in servers:
        for t in s.tools:
            if t.name in TOOL_REGISTRY:
                logger.warning("[mcp] 工具名 %s 与已有工具冲突，mcp-service 将覆盖它", t.name)
            TOOL_REGISTRY[t.name] = FuncDef(
                name=t.name,
                func=_make_mcp_call(t.name),
                description=t.description,
                label=t.name,
                metadata={"group": s.name, "source": "mcp", "source_name": s.name},
                input_schema=t.input_schema,
                output_schema=t.output_schema,
            )
            count += 1
    logger.info("[mcp] 已注册 %d 个 mcp-service 工具", count)
