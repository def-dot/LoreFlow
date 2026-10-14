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
def chwd_to_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CWD 指向 tmp_path，使 Path(stored_name) 相对路径正确解析。"""
    (tmp_path / "uploads").mkdir()
    monkeypatch.chdir(tmp_path)


async def _upload_file(client: AsyncClient, filename: str, data: bytes) -> Any:
    return await client.post("/api/v1/uploads", files={"file": (filename, data, "text/plain")})


async def _upload_docs(client: AsyncClient, items: list[dict]) -> Any:
    return await client.post("/api/v1/documents/upload", json={"items": items})


async def test_upload_and_infer_tags(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """/uploads 存文件，/documents/infer-tags 推断标签，/documents/upload 入库。"""
    import app.services.knowledge as kb

    async def _fake_infer(text: str) -> list[dict]:
        return [{"id": 99, "name": "设定"}]

    monkeypatch.setattr(kb, "infer_tags_for_text", _fake_infer)

    # 逐文件上传
    r1 = await _upload_file(client, "a.md", b"# A")
    r2 = await _upload_file(client, "b.txt", "正文".encode())
    assert r1.status_code == 201 and r2.status_code == 201
    s1, s2 = r1.json()["data"]["stored_name"], r2.json()["data"]["stored_name"]

    # 推断标签
    infer = await client.post("/api/v1/documents/infer-tags", json={"stored_names": [s1, s2]})
    assert infer.status_code == 200
    for item in infer.json()["data"]["items"]:
        assert item["suggested_tags"] == [{"id": 99, "name": "设定"}]

    # 入库
    up = await _upload_docs(client, [
        {"stored_name": s1, "filename": "a.md", "tag_ids": [99]},
        {"stored_name": s2, "filename": "b.txt", "tag_ids": []},
    ])
    assert up.status_code == 202
    items = up.json()["data"]["items"]
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
    """入库时指定 tag_ids，文档带上标签。"""
    t1 = (await client.post("/api/v1/tags", json={"name": "设定"})).json()["data"]
    t2 = (await client.post("/api/v1/tags", json={"name": "人物"})).json()["data"]

    r = await _upload_file(client, "tagged.md", "内容".encode())
    stored = r.json()["data"]["stored_name"]

    up = await _upload_docs(client, [
        {"stored_name": stored, "filename": "tagged.md", "tag_ids": [t1["id"], t2["id"]]},
    ])
    assert up.status_code == 202
    doc_id = up.json()["data"]["items"][0]["doc_id"]
    docs = (await client.get("/api/v1/documents")).json()["data"]["items"]
    doc = next(d for d in docs if d["id"] == doc_id)
    assert {t["id"] for t in doc["tags"]} == {t1["id"], t2["id"]}