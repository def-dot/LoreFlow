"""BGE-Reranker 精排服务 — 优先从 DB settings 读取配置，fallback 到 .env 的 TEI。"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)


async def _get_rerank_config() -> tuple[str, str]:
    """返回 (base_url, model_name)。优先从 settings 表读取，fallback 到 .env。"""
    try:
        from app.services.model_lookup import resolve_default_model
        base_url, _api_key, model_name = await resolve_default_model("default_rerank_model")
        return base_url, model_name
    except Exception:
        logger.debug("DB rerank 配置读取失败，fallback 到 .env", exc_info=True)

    return settings.TEI_RERANK_URL, "bge-reranker-v2-m3"


async def _rerank_batch(query: str, texts: list[str]) -> list[float]:
    """单批 rerank，返回与 texts 等长的分数列表。"""
    base_url, _model = await _get_rerank_config()
    response = await http_client().post(
        f"{base_url}/rerank",
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