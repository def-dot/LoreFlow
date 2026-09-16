"""MCP (Model Context Protocol) 客户端管理器。
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession

from pydantic import Field, create_model

from app.registry.types import TOOL_REGISTRY, FuncDef

logger = logging.getLogger(__name__)

_stacks: list[AsyncExitStack] = []


_MCP_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}


def _schema_to_input_model(tool_name: str, schema: dict[str, Any]) -> type | None:
    """将 MCP JSON Schema 转换为 Pydantic 输入模型。"""
    props = schema.get("properties", {})
    if not props:
        return None
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for pname, pschema in props.items():
        ann = _MCP_TYPE_MAP.get(pschema.get("type", "string"), str)
        desc = pschema.get("description", "")
        if pname in required:
            fields[pname] = (ann, Field(description=desc))
        else:
            fields[pname] = (ann, Field(default=None, description=desc))
    return create_model(f"{tool_name}Input", **fields) if fields else None


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

            def _make_call(sess: ClientSession, tool_name: str):
                async def _call(**kwargs: Any) -> str:
                    result = await sess.call_tool(tool_name, kwargs)
                    output = "\n".join(
                        getattr(item, "text", str(item))
                        for item in result.content
                    )
                    if result.isError:
                        raise RuntimeError(output)
                    return output
                return _call

            td = FuncDef(
                name=t.name,
                func=_make_call(session, t.name),
                description=t.description or "",
                metadata={"group": name},
                input_schema=_schema_to_input_model(t.name, t.inputSchema),
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

    for s in servers:
        await _connect_server(s)


async def shutdown_mcp() -> None:
    """关闭所有 MCP 连接。"""
    for stack in _stacks:
        try:
            await stack.aclose()
        except Exception:
            logger.debug("[mcp] 关闭资源时出错", exc_info=True)
