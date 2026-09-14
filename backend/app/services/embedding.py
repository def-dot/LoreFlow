"""Embedding 服务 — 调用 providers.yml 中配置的嵌入模型。"""

from __future__ import annotations

import logging
from typing import Any

import yaml

from app.core.config import settings
from app.utils.http import http_client

logger = logging.getLogger(__name__)


def _load_embedding_config() -> dict[str, Any]:
    with open(settings.PROVIDERS_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    emb_cfg = cfg.get("embedding") or {}
    provider_name = emb_cfg.get("provider", "")
    provider_cfg = cfg.get(provider_name) or {}
    return {
        "base_url": provider_cfg.get("base_url", ""),
        "api_key": provider_cfg.get("api_key", ""),
        "model": emb_cfg.get("model", ""),
        "is_ollama": provider_name == "ollama",
    }


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。"""
    if not texts:
        return []
    cfg = _load_embedding_config()
    if not cfg["base_url"] or not cfg["model"]:
        raise RuntimeError("未配置 embedding provider，请在 providers.yml 中设置 embedding 段")

    client = http_client()

    if cfg["is_ollama"]:
        vectors: list[list[float]] = []
        for text in texts:
            resp = await client.post(
                f"{cfg['base_url']}/api/embeddings",
                json={"model": cfg["model"], "prompt": text},
            )
            resp.raise_for_status()
            vectors.append(resp.json()["embedding"])
        return vectors

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    resp = await client.post(
        f"{cfg['base_url']}/v1/embeddings",
        json={"model": cfg["model"], "input": texts},
        headers=headers,
    )
    resp.raise_for_status()
    data = sorted(resp.json()["data"], key=lambda x: x.get("index", 0))
    return [item["embedding"] for item in data]


async def embed_query(text: str) -> list[float]:
    return (await embed_texts([text]))[0]
