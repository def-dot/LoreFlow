"""MCP 服务器管理 API — 代理到 mcp-service。"""

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.routers import mcp as mcp_router


@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """覆盖 conftest 的建表 fixture：MCP 接口不碰数据库。"""
    yield


class _FakeResp:
    def __init__(self, data: Any) -> None:
        self._data = data
        self.status_code = 200
        self.text = ""

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        pass


class _FakeClient:
    """mock http_client()，按 method+path 返回预设数据。"""

    calls: list[tuple[str, str]] = []

    async def get(self, url: str, **_: Any) -> _FakeResp:
        self.calls.append(("GET", url))
        if url.endswith("/servers"):
            return _FakeResp([])
        return _FakeResp({"mcpServers": {"echo-server": {"command": "echo", "args": ["hi"]}}})

    async def post(self, url: str, **_: Any) -> _FakeResp:
        self.calls.append(("POST", url))
        if url.endswith("/reconnect"):
            name = url.split("/")[-2]
            return _FakeResp({"name": name, "transport": "stdio", "status": "connected", "tools": []})
        return _FakeResp({"name": "echo-server", "transport": "stdio", "status": "connected", "tools": []})

    async def put(self, url: str, **_: Any) -> _FakeResp:
        self.calls.append(("PUT", url))
        return _FakeResp({"name": "renamed", "transport": "stdio", "status": "connected", "tools": []})

    async def delete(self, url: str, **_: Any) -> _FakeResp:
        self.calls.append(("DELETE", url))
        return _FakeResp({"deleted": "echo-server"})


@pytest.fixture(autouse=True)
def mcp_env(monkeypatch: pytest.MonkeyPatch):
    """mock mcp-service HTTP 调用。"""
    fake = _FakeClient()
    fake.calls = []

    async def _fake_publish(*_a: Any, **_kw: Any) -> None:
        pass

    monkeypatch.setattr(mcp_router, "http_client", lambda: fake)
    monkeypatch.setattr(mcp_router, "publish_reload", _fake_publish)
    yield fake.calls


async def test_create(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/mcp/servers", json={
        "mcpServers": {"echo-server": {"command": "echo", "args": ["hi"]}},
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "echo-server"


async def test_update_and_rename(client: AsyncClient) -> None:
    resp = await client.put("/api/v1/mcp/servers/echo-server", json={
        "mcpServers": {"renamed": {"command": "echo", "args": ["a", "b"]}},
    })
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "renamed"


async def test_delete(client: AsyncClient) -> None:
    resp = await client.delete("/api/v1/mcp/servers/echo-server")
    assert resp.status_code == 200


async def test_get_config(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/mcp/servers/echo-server")
    assert resp.status_code == 200
    assert "echo-server" in resp.json()["data"]["mcpServers"]


async def test_list_servers(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/mcp/servers")
    assert resp.status_code == 200


async def test_reconnect(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/mcp/servers/no-such/reconnect")
    assert resp.status_code == 200
