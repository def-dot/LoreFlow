"""API 测试 — 插件状态列表与运行期重载"""

from httpx import AsyncClient


async def test_plugins_list(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["msg"] == "ok"

    plugins = body["data"]["plugins"]
    notify = {p["filename"]: p for p in plugins}["notify.py"]  # conftest 已加载示例插件
    assert notify["module"] == "custom_plugins.notify"
    assert notify["node_names"] == ["notify_long_body", "notify_message"]  # 按名字排序
    assert notify["error"] is None
    assert set(notify) == {"filename", "module", "node_names", "loaded_at", "error"}


async def test_plugins_reload(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/plugins/reload")
    assert resp.status_code == 200
    plugins = resp.json()["data"]["plugins"]
    assert {p["filename"] for p in plugins} == {"notify.py"}
    assert all(p["error"] is None for p in plugins)
