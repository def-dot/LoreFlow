"""MCP (Model Context Protocol) 客户端管理器。
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession

from app.registry.tool import TOOL_REGISTRY, ParamDef, ToolDef

logger = logging.getLogger(__name__)

_stacks: list[AsyncExitStack] = []


def _schema_to_params(schema: dict[str, Any]) -> list[ParamDef]:
    """将 JSON Schema properties 转换为 ParamDef 列表。"""
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    return [
        ParamDef(
            name=pname,
            param_type=pschema.get("type", "string"),
            description=pschema.get("description", ""),
            required=pname in required,
        )
        for pname, pschema in props.items()
    ]


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
        else:
            logger.warning("[mcp] 未知 transport: %s（服务器 %s）", transport, name)
            return

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        tools_result = await session.list_tools()
        registered = 0
        for t in tools_result.tools:
            prefixed = f"{name}__{t.name}"

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

            td = ToolDef(
                name=prefixed,
                func=_make_call(session, t.name),
                description=t.description or "",
                params=_schema_to_params(t.inputSchema),
            )
            TOOL_REGISTRY[prefixed] = td
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
