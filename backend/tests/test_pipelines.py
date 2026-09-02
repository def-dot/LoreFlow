"""API 测试 — demo 流水线只读浏览（列表 + 详情 + 404/坏文件容错）"""

from httpx import AsyncClient

from app.services import pipelines as pipeline_service


async def test_pipelines_list(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/pipelines")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 200 and body["msg"] == "ok"

    pipelines = body["data"]["pipelines"]
    assert len(pipelines) >= 6
    assert set(pipelines[0]) == {"filename", "name", "description", "node_count", "params"}

    main = {p["filename"]: p for p in pipelines}["05_human_review.yaml"]
    assert main["name"] == "人工审核"
    assert main["description"]
    assert main["node_count"] == 2
    assert [p["name"] for p in main["params"]] == ["title", "content"]
    assert all(p["name"] and p["node_count"] >= 1 for p in pipelines)

    # 参数行是表单渲染与必填判断的单一事实源（不再单独输出 inputs/required_inputs）
    by_file = {p["filename"]: p for p in pipelines}
    # 11：带默认值的可选参数 → 整行形状
    assert by_file["11_loop_iteration.yaml"]["params"] == [
        {
            "name": "tick", "label": "初始计数", "description": "每轮迭代 +1 的计数器初始值（默认 0）",
            "default": 0, "has_default": True, "required": False, "multiline": False, "file": False,
        },
    ]

    # 08：两个必填参数，label/multiline 齐全
    params_08 = {p["name"]: p for p in by_file["08_dual_review.yaml"]["params"]}
    assert params_08["title"]["label"] == "标题"
    assert params_08["title"]["required"] is True and params_08["title"]["has_default"] is False
    assert params_08["content"]["description"]
    assert params_08["content"]["multiline"] is True and params_08["title"]["multiline"] is False


async def test_pipeline_detail_retry(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/pipelines/03_retry_successed.yaml")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert 'external_api["外部API<br/><i>svc_external_api · 调用外部API [R5]</i>"]' in data["mermaid"]
    svc = {n["name"]: n for n in data["nodes"]}["external_api"]
    assert svc["retry"] == "重试 5 次，退避 0.05s×2（≤0.5s），仅 TimeoutError，无抖动"


async def test_pipeline_detail_plugin(client: AsyncClient) -> None:
    """插件节点与内置节点混用：detail 里解析出插件 label/description。"""
    resp = await client.get("/api/v1/pipelines/07_plugin_demo.yaml")
    assert resp.status_code == 200
    data = resp.json()["data"]
    notify = {n["name"]: n for n in data["nodes"]}["notify"]
    assert notify["type"] == "notify_message"
    assert notify["type_label"] == "生成通知"
    assert notify["condition"] == "notify_long_body"


async def test_pipeline_unknown_404(client: AsyncClient) -> None:
    """未知文件名走白名单 404（中文 msg + 空信封）。"""
    resp = await client.get("/api/v1/pipelines/nope.yaml")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 404 and "不存在" in body["msg"] and body["data"] is None


async def test_pipeline_traversal_404(client: AsyncClient) -> None:
    """路径穿越一律 404 且不返回文件内容（Starlette 归一化或白名单拦截）。"""
    for name in ("..%2F..%2Fetc%2Fpasswd", "pipeline.yaml%2F.."):
        resp = await client.get(f"/api/v1/pipelines/{name}")
        assert resp.status_code == 404
        body = resp.json()
        assert body["data"] is None  # 任何 404 都不泄露文件内容


async def test_list_skips_broken_yaml(monkeypatch, tmp_path) -> None:
    """单个坏文件不影响列表：跳过并继续枚举其余文件。"""
    good = tmp_path / "good.yaml"
    good.write_text("name: good\nnodes:\n  a:\n    type: test_fetch\n", encoding="utf-8")
    broken = tmp_path / "broken.yaml"
    broken.write_text("nodes: [unclosed", encoding="utf-8")
    monkeypatch.setattr(pipeline_service.settings, "PIPELINES_DIR", tmp_path)

    entries = pipeline_service.list_pipelines()
    assert [e["filename"] for e in entries] == ["good.yaml"]
