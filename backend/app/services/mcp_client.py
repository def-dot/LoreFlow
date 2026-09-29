"""MCP (Model Context Protocol) 客户端管理器。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession

from app.registry.types import TOOL_REGISTRY, FuncDef

logger = logging.getLogger(__name__)

_stacks: list[AsyncExitStack] = []


_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_env(env_cfg: dict[str, Any] | None) -> dict[str, str] | None:
    """展开 env 配置中的 ${VAR} 占位符为当前进程环境变量。"""
    if not env_cfg:
        return None
    resolved: dict[str, str] = {}
    for key, value in env_cfg.items():
        text = "" if value is None else str(value)
        resolved[str(key)] = _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), text)
    return resolved


def _make_call(sess: ClientSession, tool_name: str, timeout: float | None = None):
    """把 MCP 工具包成 FuncDef 可调用的异步函数。
    """
    read_timeout = timedelta(seconds=timeout) if timeout else None

    async def _call(**kwargs: Any) -> str:
        result = await sess.call_tool(tool_name, kwargs, read_timeout_seconds=read_timeout)

        texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]

        if result.isError:
            raise RuntimeError("\n".join(texts) or "MCP 工具返回错误（无错误详情）")

        return "\n".join(texts)
    return _call


async def _connect_server(server_cfg: dict[str, Any]) -> None:
    """连接单个 MCP 服务器并注册其工具。"""
    name = server_cfg["name"]
    transport = server_cfg.get("transport", "sse")
    stack = AsyncExitStack()

    try:
        if transport == "sse":
            from mcp.client.sse import sse_client

            read, write = await stack.enter_async_context(sse_client(server_cfg["url"]))
        elif transport == "stdio":
            from mcp.client.stdio import stdio_client, StdioServerParameters

            read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=server_cfg["command"],
                args=server_cfg.get("args", []),
                env=_resolve_env(server_cfg.get("env")),
            )))
        elif transport == "http":
            from mcp.client.streamable_http import streamablehttp_client

            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(server_cfg["url"])
            )
        else:
            logger.warning("[mcp] 未知 transport: %s（服务器 %s）", transport, name)
            return

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        tools_result = await session.list_tools()
        registered = 0
        for t in tools_result.tools:
            td = FuncDef(
                name=t.name,
                func=_make_call(session, t.name, timeout=server_cfg.get("timeout", 60)),
                description=t.description or "",
                metadata={"group": name},
                input_schema=t.inputSchema,
                output_schema=t.outputSchema,
            )
            TOOL_REGISTRY[t.name] = td
            registered += 1

        _stacks.append(stack)
        logger.info("[mcp] 已连接 %s（%s），注册 %d 个工具", name, transport, registered)

    except Exception:
        logger.exception("[mcp] 连接服务器 %s 失败", name)
        await stack.aclose()


async def init_mcp(config_path: Path) -> None:
    """读取配置并连接所有 MCP 服务器。"""
    if not config_path.exists():
        logger.info("[mcp] 配置文件不存在，跳过：%s", config_path)
        return

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    servers = cfg.get("servers") or []
    if not servers:
        logger.info("[mcp] 无服务器配置")
        return

    await asyncio.gather(*[_connect_server(s) for s in servers])


async def shutdown_mcp() -> None:
    """关闭所有 MCP 连接。"""
    for stack in _stacks:
        try:
            await stack.aclose()
        except Exception:
            logger.debug("[mcp] 关闭资源时出错", exc_info=True)
