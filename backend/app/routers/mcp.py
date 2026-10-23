"""MCP 服务器运维 — /api/v1/mcp/servers

认证后代理到 mcp-service（配置 + 连接 + 工具都在那边）。
"""

from fastapi import APIRouter

from app.core.config import settings
from app.core.events import publish_reload
from app.core.response import UnifiedResponseRoute
from app.schemas.mcp import McpServerConfigIn, McpServerState
from app.utils.http import http_client

router = APIRouter(prefix="/mcp", route_class=UnifiedResponseRoute, tags=["mcp"])


# ---------------------------------------------------------------------------
# 配置增删改
# ---------------------------------------------------------------------------

@router.get("/servers/{name}", response_model=McpServerConfigIn)
async def get_server(name: str) -> McpServerConfigIn:
    resp = await http_client().get(f"{settings.MCP_SERVICE_URL}/servers/{name}/config")
    resp.raise_for_status()
    return McpServerConfigIn(**resp.json())


@router.post("/servers", response_model=McpServerState)
async def create_server(body: McpServerConfigIn) -> McpServerState:
    """接受标准 mcpServers 配置，创建并连接。"""
    resp = await http_client().post(f"{settings.MCP_SERVICE_URL}/servers", json=body.model_dump(exclude_none=True))
    resp.raise_for_status()
    state = McpServerState(**resp.json())
    await publish_reload("mcp")
    return state


@router.put("/servers/{name}", response_model=McpServerState)
async def update_server(name: str, body: McpServerConfigIn) -> McpServerState:
    """接受标准 mcpServers 配置，更新指定服务器（支持改名）。"""
    resp = await http_client().put(f"{settings.MCP_SERVICE_URL}/servers/{name}", json=body.model_dump(exclude_none=True))
    resp.raise_for_status()
    state = McpServerState(**resp.json())
    await publish_reload("mcp")
    return state


@router.delete("/servers/{name}")
async def delete_server(name: str) -> dict[str, str]:
    resp = await http_client().delete(f"{settings.MCP_SERVICE_URL}/servers/{name}")
    resp.raise_for_status()
    await publish_reload("mcp")
    return {"deleted": name}


# ---------------------------------------------------------------------------
# 运行时状态
# ---------------------------------------------------------------------------

@router.get("/servers", response_model=list[McpServerState])
async def list_servers() -> list[McpServerState]:
    resp = await http_client().get(f"{settings.MCP_SERVICE_URL}/servers")
    resp.raise_for_status()
    return [McpServerState(**s) for s in resp.json()]


@router.post("/servers/{name}/reconnect", response_model=McpServerState)
async def reconnect_server(name: str) -> McpServerState:
    resp = await http_client().post(f"{settings.MCP_SERVICE_URL}/servers/{name}/reconnect")
    resp.raise_for_status()
    return McpServerState(**resp.json())


@router.post("/servers/reconnect-all", response_model=list[McpServerState])
async def reconnect_all() -> list[McpServerState]:
    resp = await http_client().post(f"{settings.MCP_SERVICE_URL}/servers/reconnect-all")
    resp.raise_for_status()
    return [McpServerState(**s) for s in resp.json()]
