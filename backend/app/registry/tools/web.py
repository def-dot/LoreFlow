"""Web 搜索工具。"""

import logging
import os

from tavily import AsyncTavilyClient

from app.registry.tool import tool

logger = logging.getLogger(__name__)


async def _tavily_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """调用 Tavily 搜索，返回 ``[{title, url, snippet}]``。"""
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        logger.warning("未设置 TAVILY_API_KEY 环境变量，跳过搜索")
        return []

    client = AsyncTavilyClient(api_key=api_key)
    try:
        response = await client.search(query=query, max_results=max_results)
    except Exception as exc:
        logger.warning("Tavily 搜索失败: %s", exc)
        return []

    return [
        {"title": r["title"], "url": r["url"], "snippet": r.get("content", "")}
        for r in response.get("results", [])
        if r.get("title") and r.get("url")
    ]


@tool(
    name="web_search",
    description="搜索互联网获取最新信息，返回搜索结果列表（标题、链接、摘要）",
    params={"query": "搜索关键词"},
)
async def web_search_tool(query: str) -> str:
    """供 agent 节点调用的搜索工具。"""
    results = await _tavily_search(query)
    if not results:
        return "未找到相关结果"

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)
