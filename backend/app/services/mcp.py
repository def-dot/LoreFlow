"""MCP 工具注册 — 从 mcp-service 拉工具列表，注册代理 FuncDef。

CRUD 代理在 routers/mcp.py，这里只管工具注册和多 worker 同步。
"""

from __future__ import annotations

import logging

from app.core.events import on_reload
from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)


@on_reload("mcp")
async def init_mcp() -> None:
    """从 mcp-service 拉工具列表，注册到 TOOL_REGISTRY。"""
    try:
        resp = await http_client().get(f"{settings.MCP_SERVICE_URL}/servers")
        resp.raise_for_status()
        from app.schemas.mcp import McpServerState
        servers = [McpServerState(**s) for s in resp.json()]
    except Exception:
        logger.exception("[mcp] 从 mcp-service 拉取工具失败")
        return

    from app.registry.mcp_tool import register_mcp_tools
    register_mcp_tools(servers)
