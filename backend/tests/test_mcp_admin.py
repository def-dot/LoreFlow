"""MCP 服务器管理 API — 标准 mcpServers 格式、启停、连接。"""

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.core.config import settings
from app.services import mcp_client


@pytest_asyncio.fixture(autouse=True)
async def setup_db() -> AsyncGenerator[None, None]:
    """覆盖 conftest 的建表 fixture：MCP 接口不碰数据库。"""
    yield


@pytest.fixture(autouse=True)
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把配置文件指到临时目录，并快照/恢复模块级运行态。"""
    monkeypatch.setattr(settings, "MCP_CONFIG", tmp_path / "mcp.json")

    saved_servers = dict(mcp_client._servers)
    saved_stacks = dict(mcp_client._stacks)
    mcp_client._servers.clear()
    mcp_client._stacks.clear()

    yield tmp_path

    mcp_client._servers.clear()
    mcp_client._servers.update(saved_servers)
    mcp_client._stacks.clear()
    mcp_client._stacks.update(saved_stacks)


@pytest.fixture(autouse=True)
def stub_connect(monkeypatch: pytest.MonkeyPatch):
    """不真起子进程：连接成功即标 connected。"""
    async def _fake_connect(state: mcp_client.McpServerState) -> None:
        state.status = mcp_client.STATUS_CONNECTED
        state.error = None
        state.tool_names = []
        state.connected_at = None

    monkeypatch.setattr(mcp_client, "_connect_server", _fake_connect)


def _cfg_path() -> Path:
    return Path(settings.MCP_CONFIG)


def _payload(name: str = "echo-server", **server_fields: Any) -> dict[str, Any]:
    """构造标准 mcpServers 格式的请求体。"""
    server: dict[str, Any] = {"command": "echo", "args": ["hi"]}
    server.update(server_fields)
    return {"mcpServers": {name: server}}


async def test_create_writes_config_and_lists(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/mcp/servers", json=_payload())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "echo-server"

    cfg = json.loads(_cfg_path().read_text(encoding="utf-8"))
    assert "echo-server" in cfg["mcpServers"]

    resp = await client.get("/api/v1/mcp/servers")
    names = [s["name"] for s in resp.json()["data"]["servers"]]
    assert "echo-server" in names


async def test_create_duplicate_conflicts(client: AsyncClient) -> None:
    await client.post("/api/v1/mcp/servers", json=_payload())
    resp = await client.post("/api/v1/mcp/servers", json=_payload())
    assert resp.status_code == 409
    assert "已存在" in resp.json()["msg"]


async def test_update_and_rename(client: AsyncClient) -> None:
    await client.post("/api/v1/mcp/servers", json=_payload())

    resp = await client.put(
        "/api/v1/mcp/servers/echo-server",
        json=_payload(name="renamed", args=["a", "b"]),
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "renamed"

    cfg = json.loads(_cfg_path().read_text(encoding="utf-8"))
    assert "renamed" in cfg["mcpServers"]
    assert "echo-server" not in cfg["mcpServers"]


async def test_rename_collision_conflicts(client: AsyncClient) -> None:
    await client.post("/api/v1/mcp/servers", json=_payload())
    await client.post("/api/v1/mcp/servers", json=_payload(name="other"))

    resp = await client.put(
        "/api/v1/mcp/servers/echo-server",
        json=_payload(name="other"),
    )
    assert resp.status_code == 409


async def test_update_missing_is_404(client: AsyncClient) -> None:
    resp = await client.put("/api/v1/mcp/servers/nope", json=_payload())
    assert resp.status_code == 404


async def test_delete_removes_config_and_runtime(client: AsyncClient) -> None:
    await client.post("/api/v1/mcp/servers", json=_payload())
    resp = await client.delete("/api/v1/mcp/servers/echo-server")
    assert resp.status_code == 200

    cfg = json.loads(_cfg_path().read_text(encoding="utf-8"))
    assert "echo-server" not in cfg.get("mcpServers", {})
    names = [s.name for s in mcp_client.list_servers()]
    assert "echo-server" not in names


async def test_delete_missing_is_404(client: AsyncClient) -> None:
    resp = await client.delete("/api/v1/mcp/servers/nope")
    assert resp.status_code == 404


async def test_enable_is_runtime_only(client: AsyncClient) -> None:
    """启用/停用只影响内存，不写配置文件。"""
    await client.post("/api/v1/mcp/servers", json=_payload())

    resp = await client.post("/api/v1/mcp/servers/echo-server/enable", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["data"]["enabled"] is False

    # 配置文件里没有 enabled 字段
    cfg = json.loads(_cfg_path().read_text(encoding="utf-8"))
    entry = cfg["mcpServers"]["echo-server"]
    assert "enabled" not in entry

    resp = await client.post("/api/v1/mcp/servers/echo-server/enable", json={"enabled": True})
    assert resp.json()["data"]["enabled"] is True


async def test_validation_errors_in_chinese(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/mcp/servers", json=_payload(command=""))
    assert resp.status_code == 422
    assert "command" in resp.json()["msg"]

    resp = await client.post(
        "/api/v1/mcp/servers",
        json={"mcpServers": {"s": {"url": ""}}},
    )
    assert resp.status_code == 422
    assert "url" in resp.json()["msg"]


async def test_get_config_missing_is_404(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/mcp/servers/nope/config")
    assert resp.status_code == 404


async def test_init_reads_standard_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """init_mcp 能读取标准 mcpServers 格式。"""
    cfg = {
        "mcpServers": {
            "srv1": {"command": "echo", "args": ["a"]},
            "srv2": {"command": "echo", "args": ["b"]},
        }
    }
    monkeypatch.setattr(settings, "MCP_CONFIG", tmp_path / "mcp.json")
    (tmp_path / "mcp.json").write_text(json.dumps(cfg), encoding="utf-8")

    calls: list[str] = []

    async def _spy(state: mcp_client.McpServerState) -> None:
        calls.append(state.name)
        state.status = mcp_client.STATUS_CONNECTED

    monkeypatch.setattr(mcp_client, "_connect_server", _spy)
    await mcp_client.init_mcp(tmp_path / "mcp.json")

    assert set(calls) == {"srv1", "srv2"}
    assert mcp_client._servers["srv1"].transport == "stdio"
