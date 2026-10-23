"""MCP Service — 单进程持有 MCP 连接，管理 mcp.json，暴露工具和配置 API。

配置 CRUD + 连接 + 工具调用都在这里，backend 只做认证代理和工具注册。
所有 stdio 子进程只在这里跑一份。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from mcp import ClientSession
from pydantic import BaseModel, ConfigDict, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp-service")

MCP_CONFIG = Path("mcp.json")

# ── mcp.json 读写 ─────────────────────────────────────────────────

def _load_doc() -> dict[str, Any]:
    if MCP_CONFIG.exists():
        with open(MCP_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_doc(doc: dict[str, Any]) -> None:
    MCP_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    with open(MCP_CONFIG, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


# ── 连接状态 ──────────────────────────────────────────────────────

class ToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class ServerState(BaseModel):
    """MCP 服务器状态；session/stack 是连接句柄，不序列化。"""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    config: dict[str, Any] = {}
    transport: str = "http"
    status: str = "connecting"
    error: str | None = None
    tools: list[ToolInfo] = []
    session: ClientSession | None = Field(default=None, exclude=True)
    stack: AsyncExitStack | None = Field(default=None, exclude=True)


_servers: dict[str, ServerState] = {}
_lock = asyncio.Lock()


# ── 连接管理 ──────────────────────────────────────────────────────

async def _connect_server(name: str, config: dict[str, Any]) -> ServerState:
    state = ServerState(name=name, config=config, transport="stdio" if "command" in config else "http")
    _servers[name] = state
    stack = AsyncExitStack()
    try:
        if "command" in config:
            from mcp.client.stdio import StdioServerParameters, stdio_client
            read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=config["command"],
                args=config.get("args", []),
                env={**os.environ, **config.get("env", {})},
            )))
        else:
            from mcp.client.streamable_http import streamable_http_client
            read, write, _ = await stack.enter_async_context(streamable_http_client(config["url"]))

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        tools_result = await session.list_tools()
        state.tools = [
            ToolInfo(name=t.name, description=t.description or "",
                     input_schema=t.inputSchema, output_schema=t.outputSchema)
            for t in tools_result.tools
        ]

        state.session = session
        state.stack = stack
        state.status = "connected"
        logger.info("[mcp] 已连接 %s，%d 个工具", name, len(state.tools))
    except Exception as exc:
        await stack.aclose()
        logger.exception("[mcp] 连接 %s 失败", name)
        state.status = "failed"
        state.error = str(exc) or exc.__class__.__name__

    return state


async def _close_server(name: str) -> None:
    state = _servers.pop(name, None)
    if state is None:
        return
    if state.stack is not None:
        try:
            await state.stack.aclose()
        except Exception:
            logger.debug("[mcp] 关闭 %s 出错", name, exc_info=True)


# ── FastAPI ───────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    logger.info("[mcp] mcp-service 启动")
    for name, cfg in _load_doc().get("mcpServers", {}).items():
        await _connect_server(name, cfg)
    yield
    for name in list(_servers):
        await _close_server(name)


app = FastAPI(title="MCP Service", lifespan=_lifespan)


# ── 配置 CRUD ─────────────────────────────────────────────────────

class ServerPayload(BaseModel):
    mcpServers: dict[str, dict[str, Any]]


@app.get("/servers")
async def list_servers() -> list[ServerState]:
    return list(_servers.values())


@app.get("/servers/{name}/config")
async def get_server_config(name: str) -> dict[str, Any]:
    cfg = _load_doc().get("mcpServers", {}).get(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在")
    return {"mcpServers": {name: cfg}}


@app.post("/servers")
async def create_server(req: ServerPayload) -> ServerState:
    if not req.mcpServers:
        raise HTTPException(status_code=422, detail="mcpServers 不能为空")
    name, cfg = next(iter(req.mcpServers.items()))
    name = name.strip()
    doc = _load_doc()
    servers = doc.setdefault("mcpServers", {})
    if name in servers:
        raise HTTPException(status_code=409, detail=f"MCP 服务器 {name!r} 已存在")
    servers[name] = cfg
    _save_doc(doc)
    async with _lock:
        await _connect_server(name, cfg)
    return _servers[name]


@app.put("/servers/{name}")
async def update_server(name: str, req: ServerPayload) -> ServerState:
    if not req.mcpServers:
        raise HTTPException(status_code=422, detail="mcpServers 不能为空")
    new_name, cfg = next(iter(req.mcpServers.items()))
    new_name = new_name.strip()
    doc = _load_doc()
    servers = doc.setdefault("mcpServers", {})
    if name not in servers:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在")
    if new_name != name and new_name in servers:
        raise HTTPException(status_code=409, detail=f"MCP 服务器 {new_name!r} 已存在")
    servers.pop(name, None)
    servers[new_name] = cfg
    _save_doc(doc)
    async with _lock:
        await _close_server(name)
        await _connect_server(new_name, cfg)
    return _servers[new_name]


@app.delete("/servers/{name}")
async def delete_server(name: str) -> dict[str, str]:
    doc = _load_doc()
    servers = doc.setdefault("mcpServers", {})
    if name not in servers:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在")
    servers.pop(name)
    _save_doc(doc)
    async with _lock:
        await _close_server(name)
    return {"deleted": name}


@app.post("/servers/{name}/reconnect")
async def reconnect_server(name: str) -> ServerState:
    cfg = _load_doc().get("mcpServers", {}).get(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"MCP 服务器 {name} 不存在")
    async with _lock:
        await _close_server(name)
        await _connect_server(name, cfg)
    return _servers[name]


@app.post("/servers/reconnect-all")
async def reconnect_all() -> list[ServerState]:
    entries = _load_doc().get("mcpServers", {})
    async with _lock:
        for name in list(_servers):
            if name not in entries:
                await _close_server(name)
        for name, cfg in entries.items():
            existing = _servers.get(name)
            if existing is None:
                await _connect_server(name, cfg)
            elif existing.config != cfg:
                await _close_server(name)
                await _connect_server(name, cfg)
    return list(_servers.values())


# ── 工具 ──────────────────────────────────────────────────────────

class CallRequest(BaseModel):
    args: dict[str, Any] = {}


@app.post("/tools/{name}/call")
async def call_tool(name: str, req: CallRequest) -> dict[str, str]:
    state = next((s for s in _servers.values() if any(t.name == name for t in s.tools)), None)
    if state is None:
        raise ValueError(f"未知工具：{name}")
    if state.session is None:
        raise RuntimeError(f"MCP 服务器 {state.name} 未连接")
    result = await state.session.call_tool(name, req.args)
    texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
    if result.isError:
        raise RuntimeError("\n".join(texts) or "MCP 工具返回错误")
    return {"result": "\n".join(texts)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8090)))
