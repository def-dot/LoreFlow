"""上传端点与 rag_load 读盘解析 — 白名单/大小/空内容拒绝、编码探测、路径穿越、E2E。"""

import asyncio
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.registry.rag import rag_load
from app.utils.files import decode_text


async def _upload(client: AsyncClient, filename: str, data: bytes) -> Any:
    return await client.post("/api/v1/uploads", files={"file": (filename, data, "text/plain")})


async def _wait_terminal(client: AsyncClient, run_id: int, timeout: float = 15) -> dict[str, Any]:
    """轮询 run 直到终态（completed/failed/cancelled）。"""

    async def poll() -> dict[str, Any]:
        while True:
            data = (await client.get(f"/api/v1/runs/{run_id}")).json()["data"]
            if data["status"] in ("completed", "failed", "cancelled"):
                return data
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout=timeout)


async def test_upload_stores_file(client: AsyncClient) -> None:
    """上传 201：返回 {id, filename, size} 引用，磁盘字节一致。"""
    resp = await _upload(client, "北境要塞.md", "第一段设定。\n\n第二段设定。".encode())
    assert resp.status_code == 201
    doc = resp.json()["data"]
    assert doc["id"].endswith(".md")
    assert doc["filename"] == "北境要塞.md"
    assert doc["size"] == len("第一段设定。\n\n第二段设定。".encode())
    stored = settings.UPLOADS_DIR / doc["id"]
    assert stored.is_file()
    assert stored.read_bytes() == "第一段设定。\n\n第二段设定。".encode()


async def test_upload_rejects_bad_extension(client: AsyncClient) -> None:
    for filename in ("notes.pdf", "noext"):
        resp = await _upload(client, filename, b"data")
        assert resp.status_code == 400
        assert "不支持的文件类型" in resp.json()["msg"]


async def test_upload_rejects_empty(client: AsyncClient) -> None:
    resp = await _upload(client, "empty.md", b"")
    assert resp.status_code == 400
    assert "内容为空" in resp.json()["msg"]


async def test_upload_rejects_oversize(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "UPLOAD_MAX_MB", 0)
    resp = await _upload(client, "big.md", b"x")
    assert resp.status_code == 400
    assert "大小上限" in resp.json()["msg"]


def test_decode_text() -> None:
    """UTF-8 优先、GBK 兜底、replace 保底永不抛。"""
    assert decode_text("你好".encode()) == "你好"  # UTF-8 直通
    assert decode_text("你好".encode("gbk")) == "你好"  # GBK 兜底
    assert decode_text(b"ascii only") == "ascii only"
    assert decode_text(b"\xff\xff\xff\xff") == "����"  # replace 不抛


async def test_rag_load_rejects_traversal() -> None:
    """JSON 模式手输穿越 id：中文 ValueError，不碰盘上任意路径。"""
    with pytest.raises(ValueError, match="无效的文件引用"):
        await rag_load({"document": {"id": "../app/pipelines/01_serial.yaml", "filename": "x.md"}})


async def test_upload_to_run_e2e_gbk(client: AsyncClient) -> None:
    """GBK 文件端到端：上传 → 创建 01_serial run → completed，正文解码正确。"""
    gbk_bytes = "你好，北境。\n\n第二段设定。".encode("gbk")
    doc = (await _upload(client, "北境要塞.md", gbk_bytes)).json()["data"]

    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "01_serial.yaml", "inputs": {"document": {"id": doc["id"], "filename": doc["filename"]}}},
    )
    assert resp.status_code == 201
    data = await _wait_terminal(client, resp.json()["data"]["run_id"])

    assert data["status"] == "completed"
    output = data["nodes"]["文档解析"]["output"]
    assert output["doc_id"] == "北境要塞"
    assert "你好，北境。" in output["text"]
    assert data["nodes"]["写入向量库"]["status"] == "completed"


async def test_run_fails_when_file_deleted(client: AsyncClient) -> None:
    """引用的文件被清理：节点失败并报中文错误。"""
    doc = (await _upload(client, "gone.md", "内容".encode())).json()["data"]
    (settings.UPLOADS_DIR / doc["id"]).unlink()

    resp = await client.post(
        "/api/v1/runs",
        json={"config_file": "01_serial.yaml", "inputs": {"document": {"id": doc["id"], "filename": doc["filename"]}}},
    )
    assert resp.status_code == 201
    data = await _wait_terminal(client, resp.json()["data"]["run_id"])

    assert data["status"] == "failed"
    assert "不存在或已被清理" in data["nodes"]["文档解析"]["error"]
