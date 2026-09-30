"""BGE-M3 向量嵌入服务 — 通过 TEI HTTP API。"""

from __future__ import annotations

from app.core.config import settings
from app.utils.http import http_client


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """调用 TEI embedding 接口，返回稠密向量列表。"""
    response = await http_client().post(
        f"{settings.TEI_EMBED_URL}/embed",
        json={"inputs": texts, "truncate": True},
    )
    response.raise_for_status()
    return response.json()


async def embed_query(text: str) -> list[float]:
    """单条文本编码为稠密向量。"""
    results = await _embed_texts([text])
    return results[0]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量文本编码为稠密向量，自动拆分为 TEI 限制内的小批次。"""
    if not texts:
        return []
    results: list[list[float]] = []
    for i in range(0, len(texts), settings.TEI_BATCH_SIZE):
        batch = texts[i : i + settings.TEI_BATCH_SIZE]
        results.extend(await _embed_texts(batch))
    return results