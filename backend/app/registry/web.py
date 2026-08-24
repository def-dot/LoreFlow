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

from app.registry.core import node
from app.utils.http import http_client

_URL_RE = re.compile(r"""https?://[^\s<>"')\]]+""")

_CLEANUP_RES = [
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"<(script|style|svg|noscript|canvas)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL),
]
_TAG_RE = re.compile(r"<[^>]+>")

# 行为默认值
_MAX_PAGES = 3
_MAX_CHARS = 8000


def _html_to_text(html: str) -> str:
    """无依赖的 HTML → 纯文本：去注释、去 Script/Style/SVG、去标签、还原实体、压空白。"""
    text = html
    for p in _CLEANUP_RES:
        text = p.sub(" ", text)
    text = unescape(_TAG_RE.sub(" ", text))
    return " ".join(text.split())


async def _fetch_page(url: str) -> dict[str, str]:
    """抓单页 → ``{url, text}``；失败/非文本以中文注记占位，不抛出。"""
    try:
        resp = await http_client().get(url)
        resp.raise_for_status()

        ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if ctype and not (ctype.startswith("text/") or ctype.endswith(("xml", "json"))):
            return {"url": url, "text": f"（非文本内容 {ctype}，已跳过）"}

        html = resp.content.decode(resp.encoding or "utf-8", errors="replace")
    except Exception as exc:
        return {"url": url, "text": f"（抓取失败：{type(exc).__name__} {exc}）"}

    return {"url": url, "text": _html_to_text(html)[:_MAX_CHARS]}


@node(
    label="抓取链接正文",
    description="从 ctx['prompt'] 提取 http(s) 链接并发抓取网页正文",
)
async def web_fetch(ctx: dict[str, Any]) -> list[dict[str, str]]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str):
        return []

    urls = list(
        dict.fromkeys(
            u.rstrip(".,;:!?…。，；：！？、）」』》〉>\"'")
            for u in _URL_RE.findall(prompt)
        )
    )
    urls = [u for u in urls if u][:_MAX_PAGES]

    if not urls:
        return []

    return list(await asyncio.gather(*(_fetch_page(u) for u in urls)))
