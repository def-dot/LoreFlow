"""MCP 服务器配置与运维的请求/响应模型。"""

from __future__ import annotations

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
    transport: str = Field(default="http", pattern="^(sse|http)$")
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 请求 / 响应
# ---------------------------------------------------------------------------

class McpServerConfigIn(BaseModel):
    """标准 MCP 配置格式：{ "mcpServers": { "名称": { ... } } }"""

    mcpServers: dict[str, McpStdioConfig | McpHttpConfig] = Field(min_length=1)


class McpTestResultOut(BaseModel):
    ok: bool
    status: str
    tool_names: list[str] = Field(default_factory=list)
    error: str | None = None


class DeleteResult(BaseModel):
    deleted: bool = True
