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
    assert set(pipelines[0]) == {
        "filename", "name", "description", "node_count", "inputs", "required_inputs", "params",
    }

    main = {p["filename"]: p for p in pipelines}["05_human_review.yaml"]
    assert main["name"] == "人工审核"
    assert main["description"]
    assert main["node_count"] == 6
    assert main["inputs"] == {} and main["required_inputs"] == [] and main["params"] == []
    assert all(p["name"] and p["node_count"] >= 1 for p in pipelines)

    # 08/09 用 params 富声明 → 列表直接可读统一参数行（参数弹层表单渲染用）
    by_file = {p["filename"]: p for p in pipelines}
    assert by_file["08_dual_review.yaml"]["required_inputs"] == ["title", "content"]
    assert by_file["09_required_input.yaml"]["required_inputs"] == ["query"]
    assert by_file["09_required_input.yaml"]["inputs"] == {"topic": "默认主题"}

    params_08 = {p["name"]: p for p in by_file["08_dual_review.yaml"]["params"]}
    assert params_08["title"]["label"] == "标题"
    assert params_08["title"]["required"] is True and params_08["title"]["has_default"] is False
    assert params_08["content"]["description"]
    assert params_08["content"]["multiline"] is True and params_08["title"]["multiline"] is False
    params_09 = {p["name"]: p for p in by_file["09_required_input.yaml"]["params"]}
    assert params_09["query"]["required"] is True
    assert params_09["topic"] == {
        "name": "topic", "label": "主题", "description": "检索主题，不填用默认值",
        "default": "默认主题", "has_default": True, "required": False, "multiline": False,
    }


async def test_pipeline_detail_human(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/pipelines/06_human_conditional.yaml")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data) == {
        "filename", "name", "description", "node_count", "mermaid", "source", "nodes",
        "inputs", "required_inputs", "params",
    }
    assert data["name"] == "按需审核"
    assert data["mermaid"].startswith("graph TD")
    # 节点名做主行、注册表类型键+label 以小字附后（[?] 是 condition 标记）
    assert 'fetch["fetch<br/><i>cfg_fetch · 抓取原始数据</i>"]' in data["mermaid"]
    assert 'review["review<br/><i>人工审核 [?]</i>"]' in data["mermaid"]
    assert "name: 按需审核" in data["source"]

    nodes = {n["name"]: n for n in data["nodes"]}
    fetch = nodes["fetch"]
    assert fetch["kind"] == "node"
    assert fetch["type"] == "cfg_fetch"
    assert fetch["type_label"] == "抓取原始数据"
    assert fetch["type_description"]

    review = nodes["review"]
    assert review["kind"] == "human"
    assert review["type_label"] == "人工审核"
    assert review["type_description"] == "请审核合并结果。"
    assert review["condition"] == "demo_needs_review"
    assert review["condition_label"] == "演示按需审核"
    # 声明式审核视图：{key: label}（未声明 → None = 全量上下文）
    assert review["review"] == {"merge": "合并结果"}

    report = nodes["report"]
    assert report["depends_on"] == ["merge"]  # 不依赖 review：审核被跳过时照常执行


async def test_pipeline_detail_loop(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/pipelines/04_loop_iteration.yaml")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert 'batch["batch<br/><i>循环</i>"]' in data["mermaid"]
    batch = {n["name"]: n for n in data["nodes"]}["batch"]
    assert batch["kind"] == "loop"
    assert batch["type_label"] == "循环"
    assert batch["condition"] == "demo_keep_iterating"
    assert batch["condition_label"] == "演示循环条件"
    assert batch["type_description"] == "循环体 1 个节点，上限 5 轮"


async def test_pipeline_detail_retry(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/pipelines/03_retry_backoff.yaml")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert 'flaky["flaky<br/><i>demo_flaky · 演示重试 [R3]</i>"]' in data["mermaid"]
    flaky = {n["name"]: n for n in data["nodes"]}["flaky"]
    assert flaky["retry"] == "重试 3 次，退避 0.05s×2（≤0.5s），仅 RuntimeError，无抖动"


async def test_pipeline_detail_plugin(client: AsyncClient) -> None:
    """插件节点与内置节点混用：detail 里解析出插件 label/description。"""
    resp = await client.get("/api/v1/pipelines/07_plugin_demo.yaml")
    assert resp.status_code == 200
    data = resp.json()["data"]
    notify = {n["name"]: n for n in data["nodes"]}["notify"]
    assert notify["type"] == "notify_message"
    assert notify["type_label"] == "生成通知"
    assert notify["condition"] == "notify_long_body"
    assert notify["condition_label"] == "长文通知"


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
    good.write_text("name: good\nnodes:\n  a:\n    type: cfg_fetch\n", encoding="utf-8")
    broken = tmp_path / "broken.yaml"
    broken.write_text("nodes: [unclosed", encoding="utf-8")
    monkeypatch.setattr(pipeline_service.settings, "PIPELINES_DIR", tmp_path)

    entries = pipeline_service.list_pipelines()
    assert [e["filename"] for e in entries] == ["good.yaml"]
