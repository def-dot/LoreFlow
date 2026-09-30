"""BGE-Reranker-v2-m3 精排服务 — 通过 TEI HTTP API。"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)


async def _rerank_batch(query: str, texts: list[str]) -> list[float]:
    """单批 rerank，返回与 texts 等长的分数列表。"""
    response = await http_client().post(
        f"{settings.TEI_RERANK_URL}/rerank",
        json={"query": query, "texts": texts, "truncate": True},
    )
    response.raise_for_status()
    scores = sorted(response.json(), key=lambda x: x["index"])
    return [i["score"] for i in scores]


async def rerank(query: str, passages: list[str]) -> list[float]:
    """对 (query, passage) 对用 cross-encoder 打分，自动分批。"""
    if not passages:
        return []

    all_scores: list[float] = []
    for i in range(0, len(passages), settings.TEI_BATCH_SIZE):
        batch = passages[i : i + settings.TEI_BATCH_SIZE]
        try:
            scores = await _rerank_batch(query, batch)
            all_scores.extend(scores)
        except Exception:
            logger.exception("Rerank batch [%d:%d] failed, fallback to zero", i, i + len(batch))
            all_scores.extend([0.0] * len(batch))

    return all_scores