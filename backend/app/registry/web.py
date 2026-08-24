"""
Web 抓取节点 — 从提示词提取 http(s) 链接，抓取网页正文供 LLM 作参考。

输出 ``[{url, text}]``（ctx 键 = YAML 节点名；下游 ``llm_chat`` 读
``ctx["pages"]``，因此节点命名 ``pages`` 即接上）。单页失败不炸节点：
正文以中文注记占位，其余链接照常抓。外部网页走系统代理（默认
trust_env），与本地 Ollama 的绕代理策略相反。
"""

from __future__ import annotations

import asyncio
import re
from html import unescape
from typing import Any

import httpx

from app.core.config import settings
from app.registry.core import node

_URL_RE = re.compile(r"""https?://[^\s<>"')\]]+""")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """无依赖的 HTML → 纯文本：去 script/style、去标签、还原实体、压空白。"""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = unescape(_TAG_RE.sub(" ", text))
    return " ".join(text.split())


async def _fetch_page(client: httpx.AsyncClient, url: str) -> dict[str, str]:
    """抓单页 → ``{url, text}``；失败/非文本以中文注记占位，不抛出。"""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"url": url, "text": f"（抓取失败：{type(exc).__name__} {exc}）"}
    ctype = resp.headers.get("content-type", "")
    if ctype and not ctype.startswith("text/"):
        return {"url": url, "text": f"（非文本内容 {ctype}，已跳过）"}
    return {"url": url, "text": _html_to_text(resp.text)[: settings.WEB_FETCH_MAX_CHARS]}


@node(
    label="抓取链接正文",
    description="从 ctx['prompt'] 提取 http(s) 链接并发抓取网页正文，输出 [{url, text}]；无链接输出空列表",
)
async def web_fetch(ctx: dict[str, Any]) -> list[dict[str, str]]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str):
        return []
    urls = _URL_RE.findall(prompt)[: settings.WEB_FETCH_MAX_PAGES]
    if not urls:
        return []
    async with httpx.AsyncClient(
        timeout=settings.WEB_FETCH_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; LoreFlow/0.1; +local)"},
    ) as client:
        return list(await asyncio.gather(*(_fetch_page(client, u) for u in urls)))
