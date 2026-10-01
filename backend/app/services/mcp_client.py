"""MCP (Model Context Protocol) 客户端管理器。

每个服务器一条连接，状态可查询、可重连、可启停，配置的增删改写回
``mcp.json``（标准 MCP 格式）。工具注册进 ``TOOL_REGISTRY`` 并打上
``source=mcp`` 标记，供 API 区分来源。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from mcp import ClientSession
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.registry.types import TOOL_REGISTRY, FuncDef, unregister_tool
from app.schemas.mcp import McpServerConfigIn, McpStdioConfig, McpHttpConfig

logger = logging.getLogger(__name__)


_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")


# ---------------------------------------------------------------------------
# mcp.json 配置读写
# ---------------------------------------------------------------------------

def load_doc() -> dict[str, Any]:
    path = Path(settings.MCP_CONFIG)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_doc(doc: dict[str, Any]) -> None:
    path = Path(settings.MCP_CONFIG)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)



#: 连接状态
STATUS_CONNECTING = "connecting"
STATUS_CONNECTED = "connected"
STATUS_FAILED = "failed"

#: 默认工具调用超时（秒）
DEFAULT_TIMEOUT = 60


class McpServerState(BaseModel):
    """单个 MCP 服务器的配置与运行状态。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    config: McpStdioConfig | McpHttpConfig = Field(exclude=True)
    _stack: AsyncExitStack | None = None
    status: str = STATUS_CONNECTING
    error: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None

    @property
    def transport(self) -> str:
        if isinstance(self.config, McpStdioConfig):
            return "stdio"
        return "http"


#: 服务器名 → 状态
_servers: dict[str, McpServerState] = {}
_lock = asyncio.Lock()
_connect_task: asyncio.Task[None] | None = None


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _parse_config(raw: dict[str, Any]) -> McpStdioConfig | McpHttpConfig:
    """从 dict 解析出对应的配置模型。"""
    if raw.get("command"):
        return McpStdioConfig(**raw)
    return McpHttpConfig(**raw)


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
    """把 MCP 工具包成 FuncDef 可调用的异步函数。"""
    read_timeout = timedelta(seconds=timeout) if timeout else None

    async def _call(**kwargs: Any) -> str:
        result = await sess.call_tool(tool_name, kwargs, read_timeout_seconds=read_timeout)
        texts = [item.text for item in result.content if getattr(item, "type", None) == "text"]
        if result.isError:
            raise RuntimeError("\n".join(texts) or "MCP 工具返回错误（无错误详情）")
        return "\n".join(texts)
    return _call


def _drop_tools(state: McpServerState) -> None:
    """注销该服务器注册过的工具。"""
    for name in state.tool_names:
        td = TOOL_REGISTRY.get(name)
        if td is None:
            continue
        meta = td.metadata or {}
        if meta.get("source") == "mcp" and meta.get("source_name") == state.name:
            unregister_tool(name)
    state.tool_names = []


async def _connect_server(state: McpServerState) -> None:
    """连接单个 MCP 服务器并注册其工具（调用方持有 _lock）。"""
    cfg = state.config
    state.status = STATUS_CONNECTING
    state.error = None

    stack = AsyncExitStack()
    try:
        # ── 建立 transport + session ──
        if isinstance(cfg, McpStdioConfig):
            from mcp.client.stdio import StdioServerParameters, stdio_client
            read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=cfg.command,
                args=cfg.args,
                env=_resolve_env(cfg.env),
            )))
        elif isinstance(cfg, McpHttpConfig):
            from mcp.client.streamable_http import streamable_http_client
            read, write, _ = await stack.enter_async_context(streamable_http_client(cfg.url))
        else:
            raise ValueError(f"未知配置类型: {type(cfg)}")

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        # ── 注册工具 ──
        tools_result = await session.list_tools()
        for t in tools_result.tools:
            existing = TOOL_REGISTRY.get(t.name)
            if existing is not None and (existing.metadata or {}).get("source") != "mcp":
                logger.warning("[mcp] 工具名 %s 与已有工具冲突，服务器 %s 将覆盖它", t.name, state.name)
            td = FuncDef(
                name=t.name,
                func=_make_call(session, t.name, timeout=DEFAULT_TIMEOUT),
                description=t.description or "",
                label=t.name,
                metadata={"group": state.name, "source": "mcp", "source_name": state.name},
                input_schema=t.inputSchema,
                output_schema=t.outputSchema,
            )
            TOOL_REGISTRY[t.name] = td

        state.tool_names = [t.name for t in tools_result.tools]
        state._stack = stack
        state.status = STATUS_CONNECTED
        state.error = None
        state.connected_at = datetime.now(UTC)
        logger.info("[mcp] 已连接 %s（%s），注册 %d 个工具", state.name, state.transport, len(state.tool_names))
    except Exception as exc:
        await stack.aclose()
        logger.exception("[mcp] 连接服务器 %s 失败", state.name)
        state.status = STATUS_FAILED
        state.error = str(exc) or exc.__class__.__name__
        state.tool_names = []


async def _close_server(state: McpServerState) -> None:
    """断开连接并清掉工具（调用方持有 _lock）。"""
    _drop_tools(state)
    if state._stack is not None:
        try:
            await state._stack.aclose()
        except Exception:
            logger.debug("[mcp] 关闭 %s 资源时出错", state.name, exc_info=True)
        state._stack = None
    state.connected_at = None
    state.status = STATUS_CONNECTING



def _start_background_connect(names: list[str]) -> None:
    """把批量连接丢到后台，不阻塞 lifespan。"""

    async def _run() -> None:
        results = await asyncio.gather(
            *(reconnect_server(n) for n in names),
            return_exceptions=True,
        )
        for name, r in zip(names, results, strict=False):
            if isinstance(r, Exception):
                logger.warning("[mcp] 启动连接 %s 失败：%s", name, r)
            elif r.status == STATUS_CONNECTED:
                logger.info("[mcp] %s 已连接（%d 个工具）", name, len(r.tool_names))
            else:
                logger.warning("[mcp] %s 连接失败：%s", name, r.error)

    global _connect_task
    _connect_task = asyncio.create_task(_run())


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

async def init_mcp(config_path: Path) -> None:
    """读取标准 MCP 配置并注册服务器状态，连接在后台进行。异常兜底，不阻塞启动。"""
    try:
        if not config_path.exists():
            logger.info("[mcp] 配置文件不存在，跳过：%s", config_path)
            return

        entries = load_doc().get("mcpServers", {})
        if not entries:
            logger.info("[mcp] 无服务器配置")
            return

        async with _lock:
            for name, cfg in entries.items():
                _servers[name] = McpServerState(
                    name=name, config=_parse_config(cfg),
                )

        _start_background_connect(list(_servers))
    except Exception:
        logger.exception("MCP 初始化失败")


async def shutdown_mcp() -> None:
    """关闭所有 MCP 连接。"""
    task = _connect_task
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    async with _lock:
        for state in list(_servers.values()):
            await _close_server(state)
        _servers.clear()


# ---------------------------------------------------------------------------
# 运维接口（供 router 调用）
# ---------------------------------------------------------------------------

def list_servers() -> list[McpServerState]:
    """当前已知的服务器状态快照。"""
    return list(_servers.values())



async def create_server(body: McpServerConfigIn) -> McpServerState:
    """新增服务器：先写配置文件，再建运行态并连接。"""
    name, cfg = next(iter(body.mcpServers.items()))
    name = name.strip()
    cfg_dict = cfg.model_dump(exclude_none=True)
    async with _lock:
        doc = load_doc()
        servers = doc.get("mcpServers", {})
        if name in servers:
            raise HTTPException(status_code=409, detail=f"MCP 服务器 {name!r} 已存在")
        servers[name] = cfg_dict
        save_doc(doc)

        state = McpServerState(
            name=name, config=cfg,
        )
        _servers[name] = state
        await _connect_server(state)
        return state


async def update_server(name: str, body: McpServerConfigIn) -> McpServerState:
    """更新服务器（含改名）。先关旧连接再建新连接。"""
    new_name, cfg = next(iter(body.mcpServers.items()))
    new_name = new_name.strip()
    cfg_dict = cfg.model_dump(exclude_none=True)
    async with _lock:
        old = _servers.get(name)
        if old is None:
            raise KeyError(name)
        if new_name != name and load_doc().get("mcpServers", {}).get(new_name) is not None:
            raise HTTPException(status_code=409, detail=f"MCP 服务器 {new_name!r} 已存在")

        doc = load_doc()
        servers = doc.get("mcpServers", {})

        if new_name != name:
            servers.pop(name, None)
            servers[new_name] = cfg_dict
        else:
            servers[name] = cfg_dict
        save_doc(doc)

        await _close_server(old)
        _servers.pop(name, None)

        state = McpServerState(
            name=new_name,
            config=cfg,
        )
        _servers[new_name] = state
        await _connect_server(state)
        return state


async def delete_server(name: str) -> None:
    """删除服务器：先删配置文件，再断开并移除运行态。"""
    async with _lock:
        state = _servers.get(name)
        if state is None:
            raise KeyError(name)
        doc = load_doc()
        servers = doc.get("mcpServers", {})
        servers.pop(name, None)

        save_doc(doc)
        await _close_server(state)
        _servers.pop(name, None)


async def reconnect_server(name: str) -> McpServerState:
    """重连指定服务器。"""
    async with _lock:
        state = _servers.get(name)
        if state is None:
            raise KeyError(name)
        await _close_server(state)
        await _connect_server(state)
        return state


async def reconnect_all() -> list[McpServerState]:
    """重连全部已启用的服务器。"""
    return [await reconnect_server(name) for name in list(_servers)]
