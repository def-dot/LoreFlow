"""MCP 工具注册 — 将 MCP 服务器的工具注册/移除出 TOOL_REGISTRY。"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from mcp import ClientSession

from app.registry.types import TOOL_REGISTRY, FuncDef

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


def _make_call(sess: ClientSession, tool_name: str, timeout: float | None = None):
    """把 MCP 工具包成 FuncDef 可调用的异步函数。"""
    read_timeout = timedelta(seconds=timeout) if timeout else None

    async def _call(**kwargs: Any) -> str:
        result = await sess.call_tool(tool_name, kwargs, read_timeout_seconds=read_timeout)
        texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
        if result.isError:
            raise RuntimeError("\n".join(texts) or "MCP 工具返回错误（无错误详情）")
        return "\n".join(texts)
    return _call


async def register_mcp_tools(server_name: str, session: ClientSession, timeout: float = DEFAULT_TIMEOUT) -> list[str]:
    """从 MCP 会话获取工具列表并注册到 TOOL_REGISTRY，返回工具名列表。"""
    tools_result = await session.list_tools()
    for t in tools_result.tools:
        if t.name in TOOL_REGISTRY:
            logger.warning("[mcp] 工具名 %s 与已有工具冲突，服务器 %s 将覆盖它", t.name, server_name)
        td = FuncDef(
            name=t.name,
            func=_make_call(session, t.name, timeout=timeout),
            description=t.description or "",
            label=t.name,
            metadata={"group": server_name, "source": "mcp", "source_name": server_name},
            input_schema=t.inputSchema,
            output_schema=t.outputSchema,
        )
        TOOL_REGISTRY[t.name] = td
    return [t.name for t in tools_result.tools]


def unregister_mcp_tools(tool_names: list[str]) -> None:
    """从 TOOL_REGISTRY 移除指定工具。"""
    for name in tool_names:
        TOOL_REGISTRY.pop(name, None)