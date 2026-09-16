"""
Web 相关函数 — 抓取、请求、搜索。

- ``web_fetch``    从文本提取 URL 并发抓取正文（节点）
- ``http_request`` 发送 HTTP 请求（节点）
- ``web_search``   Tavily 搜索（工具）
"""


import asyncio
import os
import re
from html import unescape
from typing import Any

from tavily import AsyncTavilyClient

from pydantic import BaseModel, Field

from app.registry.types import func
from app.utils.http import http_client


class WebFetchItem(BaseModel):
    url: str = Field(description="页面链接")
    text: str = Field(description="页面正文")


class HttpRequestOutput(BaseModel):
    status_code: int = Field(description="HTTP 状态码")
    headers: dict = Field(description="响应头")
    body: Any = Field(description="响应体（自动解析 JSON）")


class HttpRequestInput(BaseModel):
    url: str = Field(description="请求 URL")
    method: str | None = Field(default=None, description="HTTP 方法（默认 GET）")
    headers: dict | None = Field(default=None, description="请求头")
    body: Any = Field(default=None, description="请求体（对象自动序列化为 JSON）")
    timeout: int | None = Field(default=None, description="超时秒数（默认 30）")


class WebSearchItem(BaseModel):
    title: str = Field(description="结果标题")
    url: str = Field(description="结果链接")
    content: str = Field(description="结果摘要")


class WebFetchOutput(BaseModel):
    result: list[WebFetchItem] = Field(description="抓取结果列表")


class WebFetchInput(BaseModel):
    url: str | list[str] = Field(description="URL 字符串或 URL 列表")


class WebSearchOutput(BaseModel):
    result: list[WebSearchItem] = Field(description="搜索结果列表")


class WebSearchInput(BaseModel):
    query: str = Field(description="搜索关键词")

# ---------------------------------------------------------------------------
# 网页抓取
# ---------------------------------------------------------------------------

_CLEANUP_RES = [
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"<(script|style|svg|noscript|canvas)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL),
]
_TAG_RE = re.compile(r"<[^>]+>")

_MAX_CHARS = 8000


async def _fetch_page(url: str) -> dict[str, str]:
    """抓单页 → ``{url, text}``；失败/非文本以中文注记占位，不抛出。"""
    try:
        resp = await http_client().get(url)
        resp.raise_for_status()

        ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if ctype and not (ctype.startswith("text/") or ctype.endswith(("xml", "json"))):
            return {"url": url, "text": f"（非文本内容 {ctype}，已跳过）"}

        raw = resp.content.decode(resp.encoding or "utf-8", errors="replace")
    except Exception as exc:
        return {"url": url, "text": f"（抓取失败：{type(exc).__name__} {exc}）"}

    # HTML → 纯文本：去注释、去 Script/Style/SVG、去标签、还原实体、压空白
    text = raw
    for p in _CLEANUP_RES:
        text = p.sub(" ", text)
    text = unescape(_TAG_RE.sub(" ", text))
    return {"url": url, "text": " ".join(text.split())[:_MAX_CHARS]}


@func(
    label="抓取链接正文",
    description="抓取一个或多个网页正文，返回 [{url, text}]",
    metadata={"group": "网络", "order": 10},
)
async def web_fetch(params: WebFetchInput) -> WebFetchOutput:
    urls = [params.url] if isinstance(params.url, str) else list(params.url)
    if not urls:
        return WebFetchOutput(result=[])

    return WebFetchOutput(result=list(await asyncio.gather(*(_fetch_page(u) for u in urls))))


# ---------------------------------------------------------------------------
# HTTP 请求
# ---------------------------------------------------------------------------

@func(
    label="HTTP 请求",
    description="发送 HTTP 请求，返回状态码、响应头和响应体",
    metadata={"group": "网络", "order": 20},
)
async def http_request(params: HttpRequestInput) -> HttpRequestOutput:
    method = (params.method or "GET").upper()
    headers = params.headers or {}
    timeout = params.timeout or 30

    kwargs: dict[str, Any] = {"method": method, "url": params.url, "headers": headers, "timeout": timeout}

    if params.body is not None:
        kwargs["json"] = params.body

    resp = await http_client().request(**kwargs)

    try:
        resp_body: Any = resp.json()
    except Exception:
        resp_body = resp.text

    return HttpRequestOutput(
        status_code=resp.status_code,
        headers=dict(resp.headers),
        body=resp_body,
    )


# ---------------------------------------------------------------------------
# Web 搜索
# ---------------------------------------------------------------------------

@func(
    label="网络搜索",
    description="搜索互联网获取最新信息，返回搜索结果列表",
    metadata={"group": "网络", "order": 30},
)
async def web_search(params: WebSearchInput) -> WebSearchOutput:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        raise RuntimeError("未设置 TAVILY_API_KEY 环境变量")

    client = AsyncTavilyClient(api_key=api_key)
    response = await client.search(query=params.query, max_results=5)

    return WebSearchOutput(result=response.get("results", []))
