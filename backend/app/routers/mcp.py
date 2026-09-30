"""MCP 服务器运维 — /api/v1/mcp/servers

运行时状态查询 + 配置增删改（写回 ``mcp.json``，标准 MCP 格式）。
"""

from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.response import UnifiedResponseRoute
from app.schemas.mcp import (
    DeleteResult,
    EnableBody,
    McpServerConfigIn,
    McpServerConfigOut,
    McpServerListResponse,
    McpServerOut,
)
from app.services import mcp_client

router = APIRouter(prefix="/mcp", route_class=UnifiedResponseRoute, tags=["mcp"])


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _endpoint_of(state: mcp_client.McpServerState) -> str:
    cfg = state.config or {}
    if state.transport == "stdio":
        args = " ".join(cfg.get("args") or [])
        return f"{cfg.get('command', '')} {args}".strip()
    return str(cfg.get("url") or "")


def _to_out(state: mcp_client.McpServerState) -> McpServerOut:
    return McpServerOut(
        name=state.name,
        transport=state.transport,
        status=state.status,
        enabled=state.enabled,
        error=state.error,
        tool_names=list(state.tool_names),
        connected_at=state.connected_at,
        endpoint=_endpoint_of(state),
    )


def _to_config_out(name: str, cfg: dict[str, Any]) -> McpServerConfigOut:
    return McpServerConfigOut(
        name=name,
        command=cfg.get("command"),
        args=cfg.get("args") or [],
        url=cfg.get("url"),
        env=cfg.get("env") or {},
        headers=cfg.get("headers") or {},
    )


# ---------------------------------------------------------------------------
# 配置增删改
# ---------------------------------------------------------------------------

@router.get("/servers/{name}/config", response_model=McpServerConfigOut)
async def get_server_config(name: str) -> McpServerConfigOut:
    for s in mcp_client.list_servers():
        if s.name == name:
            return _to_config_out(name, s.config)
    raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在")


@router.post("/servers", response_model=McpServerOut)
async def create_server(body: McpServerConfigIn) -> McpServerOut:
    """接受标准 mcpServers 配置，创建并连接。"""
    if not body.mcpServers:
        raise HTTPException(status_code=422, detail="mcpServers 不能为空")
    state = await mcp_client.create_server(body)
    return _to_out(state)


@router.put("/servers/{name}", response_model=McpServerOut)
async def update_server(name: str, body: McpServerConfigIn) -> McpServerOut:
    """接受标准 mcpServers 配置，更新指定服务器。"""
    if name not in body.mcpServers:
        raise HTTPException(status_code=422, detail=f"配置中缺少 {name}")
    try:
        state = await mcp_client.update_server(name, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在") from None
    return _to_out(state)


@router.delete("/servers/{name}", response_model=DeleteResult)
async def delete_server(name: str) -> DeleteResult:
    try:
        await mcp_client.delete_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在") from None
    return DeleteResult()


# ---------------------------------------------------------------------------
# 运行时状态
# ---------------------------------------------------------------------------

@router.get("/servers", response_model=McpServerListResponse)
async def list_servers() -> McpServerListResponse:
    return McpServerListResponse(servers=[_to_out(s) for s in mcp_client.list_servers()])


@router.post("/servers/{name}/reconnect", response_model=McpServerOut)
async def reconnect_server(name: str) -> McpServerOut:
    try:
        state = await mcp_client.reconnect_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在") from None
    return _to_out(state)


@router.post("/servers/reconnect-all", response_model=McpServerListResponse)
async def reconnect_all() -> McpServerListResponse:
    states = await mcp_client.reconnect_all()
    return McpServerListResponse(servers=[_to_out(s) for s in states])


@router.post("/servers/{name}/enable", response_model=McpServerOut)
async def set_enabled(name: str, body: EnableBody) -> McpServerOut:
    try:
        state = await mcp_client.set_server_enabled(name, body.enabled)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在") from None
    return _to_out(state)
