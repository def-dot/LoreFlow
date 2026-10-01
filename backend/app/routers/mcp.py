"""MCP 服务器运维 — /api/v1/mcp/servers

运行时状态查询 + 配置增删改（写回 ``mcp.json``，标准 MCP 格式）。
"""

from fastapi import APIRouter, HTTPException

from app.core.response import UnifiedResponseRoute
from app.schemas.mcp import DeleteResult, McpServerConfigIn
from app.services import mcp_client
from app.services.mcp_client import McpServerState

router = APIRouter(prefix="/mcp", route_class=UnifiedResponseRoute, tags=["mcp"])


# ---------------------------------------------------------------------------
# 配置增删改
# ---------------------------------------------------------------------------

@router.get("/servers/{name}", response_model=McpServerConfigIn)
async def get_server(name: str) -> McpServerConfigIn:
    cfg = mcp_client.load_doc().get("mcpServers", {}).get(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在")
    return McpServerConfigIn(mcpServers={name: cfg})


@router.post("/servers", response_model=McpServerState)
async def create_server(body: McpServerConfigIn) -> McpServerState:
    """接受标准 mcpServers 配置，创建并连接。"""
    return await mcp_client.create_server(body)


@router.put("/servers/{name}", response_model=McpServerState)
async def update_server(name: str, body: McpServerConfigIn) -> McpServerState:
    """接受标准 mcpServers 配置，更新指定服务器（支持改名）。"""
    try:
        return await mcp_client.update_server(name, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在") from None


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

@router.get("/servers", response_model=list[McpServerState])
async def list_servers() -> list[McpServerState]:
    return mcp_client.list_servers()


@router.post("/servers/{name}/reconnect", response_model=McpServerState)
async def reconnect_server(name: str) -> McpServerState:
    try:
        return await mcp_client.reconnect_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在") from None


@router.post("/servers/reconnect-all", response_model=list[McpServerState])
async def reconnect_all() -> list[McpServerState]:
    return await mcp_client.reconnect_all()