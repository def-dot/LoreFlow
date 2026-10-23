"""MCP 服务器配置与运维的请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 传输配置 — stdio / HTTP(SSE + Streamable HTTP)
# ---------------------------------------------------------------------------

class McpStdioConfig(BaseModel):
    """stdio 传输：本地进程。"""

    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class McpHttpConfig(BaseModel):
    """HTTP 传输：远程服务。"""

    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 请求 / 响应
# ---------------------------------------------------------------------------

class McpServerConfigIn(BaseModel):
    """标准 MCP 配置格式：{ "mcpServers": { "名称": { ... } } }"""

    mcpServers: dict[str, McpStdioConfig | McpHttpConfig] = Field(min_length=1)


class McpToolInfo(BaseModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None


class McpServerState(BaseModel):
    """MCP 服务器运行状态。"""

    name: str
    transport: str = "http"
    status: str = "connecting"
    error: str | None = None
    tools: list[McpToolInfo] = Field(default_factory=list)
