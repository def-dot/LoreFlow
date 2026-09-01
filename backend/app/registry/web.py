"""
Web 抓取节点 — 从提示词提取 http(s) 链接，抓取网页正文供 LLM 作参考。

输出 ``[{url, text}]``（ctx 键 = YAML 节点名；下游 ``llm_chat`` 读
``ctx["pages"]``，因此节点命名 ``pages`` 即接上）。单页失败不炸节点：
正文以中文注记占位，其余链接照常抓。外部网页走系统代理（默认
trust_env），与本地 Ollama 的绕代理策略相反。
"""

from __future__ import annotations

import asyncio
import logging
import re
from html import unescape
from typing import Any

import httpx

from app.registry.core import node_type
from app.utils.http import http_client

logger = logging.getLogger(__name__)

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


@node_type(
    label="网络搜索",
    description="根据 ctx['prompt'] 调用 DuckDuckGo 搜索，返回 [{title, url, snippet}]",
)
async def web_search(ctx: dict[str, Any]) -> list[dict[str, str]]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return []

    url = "https://html.duckduckgo.com/html/"
    try:
        resp = await http_client().post(url, data={"q": prompt}, timeout=15.0)
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        logger.warning("DuckDuckGo 搜索失败: %s", exc)
        return []

    results: list[dict[str, str]] = []
    # 解析搜索结果：DuckDuckGo HTML 版返回结构化的 result 标签
    for match in re.finditer(
        r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        re.DOTALL,
    ):
        link = unescape(match.group(1))
        title = unescape(_TAG_RE.sub("", match.group(2))).strip()
        snippet = unescape(_TAG_RE.sub("", match.group(3))).strip()
        if title and snippet:
            results.append({"title": title, "url": link, "snippet": snippet})
        if len(results) >= 5:
            break
    return results


@node_type(
    label="搜索结果格式化",
    description="读取 ctx['search']（web_search 输出），格式化为可读文本写入 ctx['context']",
)
async def search_format(ctx: dict[str, Any]) -> str:
    results = ctx.get("search")
    if not isinstance(results, list) or not results:
        return ""
    return "\n\n".join(
        f"[{i}] {r.get('title', '')}\n{r.get('url', '')}\n{r.get('snippet', '')}"
        for i, r in enumerate(results, 1)
        if isinstance(r, dict)
    )


@node_type(
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
