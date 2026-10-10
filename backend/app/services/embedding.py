"""BGE-M3 向量嵌入服务 — 优先从 DB settings 读取配置，fallback 到 .env 的 TEI。"""

from __future__ import annotations

import logging

from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)


async def _get_embed_config() -> tuple[str, str]:
    """返回 (base_url, model_name)。优先从 settings 表读取，fallback 到 .env。"""
    try:
        from app.services.model_lookup import resolve_default_model
        base_url, _api_key, model_name = await resolve_default_model("default_embedding_model")
        return base_url, model_name
    except Exception:
        logger.debug("DB embedding 配置读取失败，fallback 到 .env", exc_info=True)

    return settings.TEI_EMBED_URL, "bge-m3"


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """调用 embedding 接口，返回稠密向量列表。"""
    base_url, _model = await _get_embed_config()
    response = await http_client().post(
        f"{base_url}/embed",
        json={"inputs": texts, "truncate": True},
    )
    response.raise_for_status()
    return response.json()


async def embed_query(text: str) -> list[float]:
    """单条文本编码为稠密向量。"""
    results = await _embed_texts([text])
    return results[0]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量文本编码为稠密向量，自动拆分为小批次。"""
    if not texts:
        return []
    results: list[list[float]] = []
    for i in range(0, len(texts), settings.TEI_BATCH_SIZE):
        batch = texts[i : i + settings.TEI_BATCH_SIZE]
        results.extend(await _embed_texts(batch))
    return results