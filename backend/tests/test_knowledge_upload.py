"""知识库两阶段上传 — /uploads 存文件，/documents/infer-tags 推断标签，/documents/upload 入库。"""

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

import app.utils.arq as arq_mod


@pytest.fixture(autouse=True)
def stub_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕过 Redis：解析入队改为无操作。"""

    async def _noop(_doc_id: int) -> bool:
        return True

    monkeypatch.setattr(arq_mod, "enqueue_parse", _noop)


@pytest.fixture(autouse=True)
def stub_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """绕过 Redis：取消任务改为无操作（delete_document 会调 cancel_parse）。"""

    async def _noop(_doc_id: int) -> bool:
        return False

    monkeypatch.setattr(arq_mod, "cancel_parse", _noop)


@pytest.fixture(autouse=True)
def chwd_to_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CWD 指向 tmp_path，使 Path(store_path) 相对路径正确解析。"""
    (tmp_path / "uploads").mkdir()
    monkeypatch.chdir(tmp_path)


async def _upload_file(client: AsyncClient, filename: str, data: bytes) -> Any:
    return await client.post("/api/v1/uploads", files={"file": (filename, data, "text/plain")})


async def _upload_docs(client: AsyncClient, items: list[dict]) -> Any:
    return await client.post("/api/v1/documents/upload", json={"items": items})


async def test_upload_and_infer_tags(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """/uploads 存文件，/documents/infer-tags 推断标签，/documents/upload 入库。"""
    import app.services.knowledge as kb

    async def _fake_infer(text: str) -> list[str]:
        return ["设定"]

    monkeypatch.setattr(kb, "infer_tags_for_text", _fake_infer)

    # 逐文件上传
    r1 = await _upload_file(client, "a.md", b"# A")
    r2 = await _upload_file(client, "b.txt", "正文".encode())
    assert r1.status_code == 201 and r2.status_code == 201
    s1, s2 = r1.json()["data"]["store_path"], r2.json()["data"]["store_path"]

    # 推断标签
    infer = await client.post("/api/v1/documents/infer-tags", json={"store_paths": [s1, s2]})
    assert infer.status_code == 200
    for item in infer.json()["data"]["items"]:
        assert item["suggested_tags"] == ["设定"]

    # 入库
    up = await _upload_docs(client, [
        {"store_path": s1, "filename": "a.md", "tags": ["设定"]},
        {"store_path": s2, "filename": "b.txt", "tags": []},
    ])
    assert up.status_code == 202
    items = up.json()["data"]
    assert [i["filename"] for i in items] == ["a.md", "b.txt"]
    for i in items:
        assert i["error"] is None
        assert i["doc_id"] is not None
    # 文档记录能定位到盘上文件
    doc = (await client.get(f"/api/v1/documents/{items[0]['doc_id']}")).json()["data"]
    assert doc["file_size"] == len(b"# A")


async def test_upload_rejects_bad_extension(client: AsyncClient) -> None:
    """/uploads 拒绝非法后缀。"""
    resp = await _upload_file(client, "bad.xlsx", b"x")
    assert resp.status_code == 400
    assert "不支持的文件类型" in resp.json()["msg"]


async def test_upload_with_tags(client: AsyncClient) -> None:
    """入库时按标签名关联；未登记的标签自动创建。"""
    await client.post("/api/v1/tags", json={"name": "设定"})

    r = await _upload_file(client, "tagged.md", "内容".encode())
    stored = r.json()["data"]["store_path"]

    up = await _upload_docs(client, [
        {"store_path": stored, "filename": "tagged.md", "tags": ["设定", "人物"]},
    ])
    assert up.status_code == 202
    doc_id = up.json()["data"][0]["doc_id"]
    docs = (await client.get("/api/v1/documents")).json()["data"]["items"]
    doc = next(d for d in docs if d["id"] == doc_id)
    assert set(doc["tags"]) == {"设定", "人物"}
    # 「人物」被自动登记进标签表
    assert set((await client.get("/api/v1/tags")).json()["data"]) >= {"设定", "人物"}


async def test_rename_tag_updates_documents(client: AsyncClient) -> None:
    """重命名标签后，文档上的标签跟着变；按标签名筛选仍命中。"""
    r = await _upload_file(client, "a.md", "内容".encode())
    stored = r.json()["data"]["store_path"]
    up = await _upload_docs(client, [{"store_path": stored, "filename": "a.md", "tags": ["旧名"]}])
    doc_id = up.json()["data"][0]["doc_id"]

    renamed = await client.put("/api/v1/tags/旧名", json={"name": "新名"})
    assert renamed.status_code == 200 and renamed.json()["data"] == {"name": "新名"}

    doc = (await client.get(f"/api/v1/documents/{doc_id}")).json()["data"]
    assert doc["tags"] == ["新名"]

    hit = await client.get("/api/v1/documents", params={"tags": "新名"})
    assert [d["id"] for d in hit.json()["data"]["items"]] == [doc_id]
    miss = await client.get("/api/v1/documents", params={"tags": "旧名"})
    assert miss.json()["data"]["items"] == []


# ── 同名冲突预检 ───────────────────────────────────────────────────


async def test_upload_same_name_rejected(client: AsyncClient) -> None:
    """同名文档：整批中止，返回 error。"""
    r1 = await _upload_file(client, "a.md", b"# V1")
    stored1 = r1.json()["data"]["store_path"]
    up1 = await _upload_docs(client, [{"store_path": stored1, "filename": "a.md", "tags": []}])
    assert up1.json()["data"][0]["doc_id"] is not None

    # 同名再次入库
    r2 = await _upload_file(client, "a.md", b"# V2")
    stored2 = r2.json()["data"]["store_path"]
    up2 = await _upload_docs(client, [{"store_path": stored2, "filename": "a.md", "tags": []}])
    items = up2.json()["data"]
    assert items[0]["doc_id"] is None
    assert "同名文档" in items[0]["error"]

    # 总数仍为 1
    total = (await client.get("/api/v1/documents")).json()["data"]["total"]
    assert total == 1


async def test_upload_batch_rejected_on_any_duplicate(client: AsyncClient) -> None:
    """批量入库中有一个同名，整批中止，其余也不入库。"""
    r1 = await _upload_file(client, "exist.md", b"old")
    stored1 = r1.json()["data"]["store_path"]
    await _upload_docs(client, [{"store_path": stored1, "filename": "exist.md", "tags": []}])

    # 上传新文件 + 同名文件
    r_new = await _upload_file(client, "new.md", b"new")
    r_dup = await _upload_file(client, "exist.md", b"dup")
    up = await _upload_docs(client, [
        {"store_path": r_new.json()["data"]["store_path"], "filename": "new.md", "tags": []},
        {"store_path": r_dup.json()["data"]["store_path"], "filename": "exist.md", "tags": []},
    ])
    items = up.json()["data"]
    # 整批中止：new.md 也没入库
    assert items[0]["doc_id"] is None
    assert items[0]["error"] is None
    assert items[1]["error"] is not None

    total = (await client.get("/api/v1/documents")).json()["data"]["total"]
    assert total == 1  # 只有最初那个


async def test_delete_document_cleans_disk(client: AsyncClient) -> None:
    """DELETE /documents/{id} 清理暂存文件和 parsed json。"""
    from app.core.config import settings

    data = b"# To delete"
    r = await _upload_file(client, "del.md", data)
    stored = r.json()["data"]["store_path"]
    up = await _upload_docs(client, [{"store_path": stored, "filename": "del.md", "tags": []}])
    doc_id = up.json()["data"][0]["doc_id"]

    staged_file = settings.UPLOADS_DIR / Path(stored).name
    parsed_json = Path("parsed", f"{doc_id}.json")
    parsed_json.parent.mkdir(parents=True, exist_ok=True)
    parsed_json.write_text("{}")
    assert staged_file.exists()

    resp = await client.delete(f"/api/v1/documents/{doc_id}")
    assert resp.status_code == 200

    assert not staged_file.exists()
    assert not parsed_json.exists()