"""MCP 服务器配置与运维的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime

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
    """SSE / Streamable HTTP 传输：远程服务。"""

    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 请求 / 响应
# ---------------------------------------------------------------------------

class McpServerConfigIn(BaseModel):
    """标准 MCP 配置格式：{ "mcpServers": { "名称": { ... } } }"""

    mcpServers: dict[str, McpStdioConfig | McpHttpConfig]


class McpServerConfigOut(BaseModel):
    """标准 MCP 配置视图。"""

    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)


class McpServerOut(BaseModel):
    """运行时视图——不含 env 密钥。"""

    name: str
    transport: str
    status: str
    enabled: bool
    error: str | None = None
    tool_names: list[str] = Field(default_factory=list)
    connected_at: datetime | None = None
    endpoint: str = ""


class McpServerListResponse(BaseModel):
    servers: list[McpServerOut] = Field(default_factory=list)


class EnableBody(BaseModel):
    enabled: bool


class McpTestResultOut(BaseModel):
    ok: bool
    status: str
    tool_names: list[str] = Field(default_factory=list)
    error: str | None = None


class DeleteResult(BaseModel):
    deleted: bool = True
