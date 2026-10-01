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

from mcp import ClientSession
from pydantic import BaseModel, Field

from app.core.config import settings
from app.registry.types import TOOL_REGISTRY, FuncDef
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
    """单个 MCP 服务器的运行状态。"""

    name: str
    config: McpStdioConfig | McpHttpConfig = Field(exclude=True)
    status: str = STATUS_CONNECTING
    error: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None

    @property
    def transport(self) -> str:
        return "stdio" if isinstance(self.config, McpStdioConfig) else "http"


#: 服务器名 → 状态
_servers: dict[str, McpServerState] = {}
_stacks: dict[str, AsyncExitStack] = {}
_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------



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



async def _connect_server(name: str, config: McpStdioConfig | McpHttpConfig) -> McpServerState:
    """创建状态、连接服务器、注册工具，放入 _servers（调用方持有 _lock）。"""
    state = McpServerState(name=name, config=config)
    _servers[name] = state

    stack = AsyncExitStack()
    try:
        # ── 建立 transport + session ──
        if isinstance(config, McpStdioConfig):
            from mcp.client.stdio import StdioServerParameters, stdio_client
            read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=config.command,
                args=config.args,
                env=_resolve_env(config.env),
            )))
        elif isinstance(config, McpHttpConfig):
            from mcp.client.streamable_http import streamable_http_client
            read, write, _ = await stack.enter_async_context(streamable_http_client(config.url))
        else:
            raise ValueError(f"未知配置类型: {type(config)}")

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        # ── 注册工具 ──
        tools_result = await session.list_tools()
        for t in tools_result.tools:
            if t.name in TOOL_REGISTRY:
                logger.warning("[mcp] 工具名 %s 与已有工具冲突，服务器 %s 将覆盖它", t.name, name)
            td = FuncDef(
                name=t.name,
                func=_make_call(session, t.name, timeout=DEFAULT_TIMEOUT),
                description=t.description or "",
                label=t.name,
                metadata={"group": name, "source": "mcp", "source_name": name},
                input_schema=t.inputSchema,
                output_schema=t.outputSchema,
            )
            TOOL_REGISTRY[t.name] = td

        state.tool_names = [t.name for t in tools_result.tools]
        _stacks[name] = stack
        state.status = STATUS_CONNECTED
        state.error = None
        state.connected_at = datetime.now(UTC)
        logger.info("[mcp] 已连接 %s（%s），注册 %d 个工具", name, state.transport, len(state.tool_names))
    except Exception as exc:
        await stack.aclose()
        logger.exception("[mcp] 连接服务器 %s 失败", name)
        state.status = STATUS_FAILED
        state.error = str(exc) or exc.__class__.__name__
        state.tool_names = []

    return state


async def _close_server(name: str) -> None:
    """断开连接、清掉工具、移除运行态（调用方持有 _lock）。"""
    state = _servers.pop(name, None)
    if state is None:
        return
    for t in state.tool_names:
        TOOL_REGISTRY.pop(t, None)
    stack = _stacks.pop(name, None)
    if stack is not None:
        try:
            await stack.aclose()
        except Exception:
            logger.debug("[mcp] 关闭 %s 资源时出错", name, exc_info=True)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------

async def init_mcp(config_path: Path) -> None:
    """读取标准 MCP 配置，后台连接所有服务器。"""
    try:
        if not config_path.exists():
            logger.info("[mcp] 配置文件不存在，跳过：%s", config_path)
            return

        entries = load_doc().get("mcpServers", {})
        if not entries:
            logger.info("[mcp] 无服务器配置")
            return

        async def _connect_all() -> None:
            async with _lock:
                for name, cfg in entries.items():
                    cfg_model = McpStdioConfig(**cfg) if cfg.get("command") else McpHttpConfig(**cfg)
                    try:
                        await _connect_server(name, cfg_model)
                    except Exception:
                        logger.exception("[mcp] 连接服务器 %s 失败", name)

        asyncio.create_task(_connect_all())
    except Exception:
        logger.exception("MCP 初始化失败")


async def shutdown_mcp() -> None:
    """关闭所有 MCP 连接。"""
    async with _lock:
        for name in list(_servers):
            await _close_server(name)


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
    async with _lock:
        doc = load_doc()
        servers = doc.get("mcpServers", {})
        if name in servers:
            raise ValueError(f"MCP 服务器 {name!r} 已存在")
        
        servers[name] = cfg.model_dump(exclude_none=True)
        save_doc(doc)

        return await _connect_server(name, cfg)


async def update_server(name: str, body: McpServerConfigIn) -> McpServerState:
    """更新服务器（含改名）。先关旧连接再建新连接。"""
    new_name, cfg = next(iter(body.mcpServers.items()))
    new_name = new_name.strip()
    async with _lock:
        doc = load_doc()
        servers = doc.get("mcpServers", {})
        if name not in servers:
            raise ValueError(f"MCP 服务器 {name} 不存在") from None
        if new_name != name and new_name in servers:
            raise ValueError(f"MCP 服务器 {new_name!r} 已存在")

        servers.pop(name, None)
        servers[new_name] = cfg.model_dump(exclude_none=True)
        save_doc(doc)

        await _close_server(name)
        return await _connect_server(new_name, cfg)


async def delete_server(name: str) -> None:
    """删除服务器：先删配置文件，再断开并移除运行态。"""
    async with _lock:
        doc = load_doc()
        servers = doc.get("mcpServers", {})
        if name not in servers:
            raise ValueError(f"MCP 服务器 {name} 不存在")
        servers.pop(name)
        save_doc(doc)
        await _close_server(name)


async def reconnect_server(name: str) -> McpServerState:
    """重连指定服务器。"""
    async with _lock:
        old = _servers.get(name)
        if old is None:
            raise ValueError(f"MCP 服务器 {name} 不存在")
        await _close_server(name)
        return await _connect_server(name, old.config)


async def reconnect_all() -> list[McpServerState]:
    """重连全部已启用的服务器。"""
    return [await reconnect_server(name) for name in list(_servers)]
