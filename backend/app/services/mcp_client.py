"""MCP (Model Context Protocol) 客户端管理器。

连接配置文件中的 MCP 服务器，自动发现并注册工具到 MCP_TOOL_REGISTRY。
应用 lifespan 调用 init_mcp / shutdown_mcp 管理生命周期。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from mcp import ClientSession

from app.registry.tool import ParamDef, ToolDef

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 全局状态
# ---------------------------------------------------------------------------

# 工具名（带前缀）→ (ToolDef, 原始工具名, session)
_MCP_TOOL_REGISTRY: dict[str, tuple[ToolDef, str, ClientSession]] = {}

# 服务器名 → 用于清理的资源
_connections: dict[str, dict[str, Any]] = {}

# 工具名前缀分隔符
_SEP = "__"


def _prefixed(server_name: str, tool_name: str) -> str:
    return f"{server_name}{_SEP}{tool_name}"


def _strip_prefix(name: str) -> tuple[str, str]:
    """拆分 prefixed name → (server_name, tool_name)。"""
    if _SEP in name:
        server, tool = name.split(_SEP, 1)
        return server, tool
    return "", name


# ---------------------------------------------------------------------------
# 类型映射
# ---------------------------------------------------------------------------

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


async def _mcp_func_placeholder(**_kwargs: Any) -> str:
    """MCP 工具占位函数，不应被直接调用。"""
    raise RuntimeError("MCP 工具必须通过 session.call_tool 调用")


def _schema_to_params(schema: dict[str, Any]) -> list[ParamDef]:
    """将 JSON Schema properties 转换为 ParamDef 列表。"""
    props = schema.get("properties", {})
    required = set(schema.get("required", []))
    params: list[ParamDef] = []
    for pname, pschema in props.items():
        ptype = _JSON_TYPE_MAP.get(pschema.get("type", "string"), str)
        params.append(ParamDef(
            name=pname,
            param_type=ptype,
            description=pschema.get("description", ""),
            required=pname in required,
        ))
    return params


# ---------------------------------------------------------------------------
# 连接管理
# ---------------------------------------------------------------------------

async def _connect_server(server_cfg: dict[str, Any]) -> None:
    """连接单个 MCP 服务器并注册其工具。"""
    name = server_cfg["name"]
    transport = server_cfg.get("transport", "sse")

    session: ClientSession
    ctx_resources: list[Any] = []

    try:
        if transport == "sse":
            from mcp.client.sse import sse_client

            url = server_cfg["url"]
            sse_ctx = sse_client(url)
            read, write = await sse_ctx.__aenter__()
            ctx_resources.append(sse_ctx)

            session_ctx = ClientSession(read, write)
            session = await session_ctx.__aenter__()
            ctx_resources.append(session_ctx)

        elif transport == "stdio":
            from mcp.client.stdio import stdio_client, StdioServerParameters

            params = StdioServerParameters(
                command=server_cfg["command"],
                args=server_cfg.get("args", []),
            )
            stdio_ctx = stdio_client(params)
            read, write = await stdio_ctx.__aenter__()
            ctx_resources.append(stdio_ctx)

            session_ctx = ClientSession(read, write)
            session = await session_ctx.__aenter__()
            ctx_resources.append(session_ctx)

        else:
            logger.warning("[mcp] 未知 transport: %s（服务器 %s）", transport, name)
            return

        await session.initialize()

        # 发现工具
        tools_result = await session.list_tools()
        registered = 0
        for t in tools_result.tools:
            prefixed = _prefixed(name, t.name)
            params = _schema_to_params(t.inputSchema if hasattr(t, "inputSchema") else {})
            td = ToolDef(
                name=prefixed,
                func=_mcp_func_placeholder,
                description=t.description or "",
                params=params,
            )
            _MCP_TOOL_REGISTRY[prefixed] = (td, t.name, session)
            registered += 1

        _connections[name] = {"session": session, "resources": ctx_resources}
        logger.info("[mcp] 已连接 %s（%s），注册 %d 个工具", name, transport, registered)

    except Exception:
        logger.exception("[mcp] 连接服务器 %s 失败", name)
        # 清理已打开的资源
        for ctx in reversed(ctx_resources):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                pass


async def _disconnect_all() -> None:
    """关闭所有 MCP 连接。"""
    for name, conn in _connections.items():
        for ctx in reversed(conn.get("resources", [])):
            try:
                await ctx.__aexit__(None, None, None)
            except Exception:
                logger.debug("[mcp] 关闭 %s 资源时出错", name, exc_info=True)
    _connections.clear()
    _MCP_TOOL_REGISTRY.clear()


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

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
    await _disconnect_all()


def list_mcp_tools() -> list[ToolDef]:
    """返回所有已注册的 MCP 工具定义。"""
    return [td for td, _, _ in _MCP_TOOL_REGISTRY.values()]


def get_mcp_tool(name: str) -> tuple[str, ClientSession] | None:
    """查找 MCP 工具，返回 (原始工具名, session)；未找到返回 None。"""
    entry = _MCP_TOOL_REGISTRY.get(name)
    if entry is None:
        return None
    _, original_name, session = entry
    return original_name, session
