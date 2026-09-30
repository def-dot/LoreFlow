"""能力目录 API — 节点/工具的 roles·source 标注，技能重扫，MCP 服务器状态。"""

from httpx import AsyncClient


async def test_node_types_carry_roles_and_source(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/node-types")
    assert resp.status_code == 200
    items = resp.json()["data"]
    by_name = {t["name"]: t for t in items}

    # 双注册：既是节点又是工具
    both = by_name["read_document"]
    assert set(both["roles"]) == {"node", "tool"}
    assert both["source"]["kind"] == "builtin"

    # notify.py 是 tool=False，只出现在节点侧，来源标为插件
    plugin_node = by_name["send_email"]
    assert plugin_node["roles"] == ["node"]
    assert plugin_node["source"] == {"kind": "plugin", "name": "notify.py"}


async def test_tools_carry_roles_and_source(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/tools")
    assert resp.status_code == 200
    items = resp.json()["data"]
    by_name = {t["name"]: t for t in items}

    # node=False 的仅工具条目现在能被看见
    assert by_name["load_skill"]["roles"] == ["tool"]
    assert by_name["load_skill"]["source"]["kind"] == "builtin"
    assert by_name["run_code"]["roles"] == ["tool"]

    # send_email 是 tool=False，不应出现在工具列表
    assert "send_email" not in by_name

    # 工具带 schema，前端卡片可直接渲染
    assert by_name["run_code"]["input_schema"] is not None


async def test_skills_list_and_rescan(client: AsyncClient, monkeypatch, tmp_path) -> None:
    from app.core.config import settings

    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: demo\ndescription: 演示技能\n---\n\n# 演示\n\n正文内容。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "SKILLS_DIR", tmp_path / "skills")

    resp = await client.post("/api/v1/skills/rescan")
    assert resp.status_code == 200
    assert resp.json()["data"]["count"] == 1

    resp = await client.get("/api/v1/skills")
    skills = resp.json()["data"]
    assert [s["name"] for s in skills] == ["demo"]
    assert skills[0]["description"] == "演示技能"
    assert "正文内容" in skills[0]["body"]


async def test_mcp_servers_endpoints(client: AsyncClient, monkeypatch, tmp_path) -> None:
    from app.core.config import settings
    from app.services import mcp_client

    # 启停会写回 mcp.json，隔离到临时文件，别污染 backend/mcp.json
    monkeypatch.setattr(settings, "MCP_CONFIG", tmp_path / "mcp.json")

    mcp_client._servers.clear()
    mcp_client._servers["demo-server"] = mcp_client.McpServerState(
        name="demo-server",
        transport="stdio",
        config={"name": "demo-server", "transport": "stdio", "command": "echo"},
        status=mcp_client.STATUS_FAILED,
        error="connection refused",
        tool_names=["do_thing"],
    )

    resp = await client.get("/api/v1/mcp/servers")
    servers = resp.json()["data"]["servers"]
    demo = next(s for s in servers if s["name"] == "demo-server")
    assert demo["status"] == "failed"
    assert demo["error"] == "connection refused"
    assert demo["tool_names"] == ["do_thing"]
    assert demo["endpoint"] == "echo"
    # env 里的密钥不外泄
    assert "env" not in demo

    resp = await client.post("/api/v1/mcp/servers/demo-server/enable", json={"enabled": False})
    assert resp.json()["data"]["enabled"] is False
    assert resp.json()["data"]["status"] == "disabled"

    resp = await client.post("/api/v1/mcp/servers/no-such/reconnect")
    assert resp.status_code == 404
