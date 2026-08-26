"""
RAG 演示节点 — 文档入库与知识库检索。

入库链（01_serial.yaml）：rag_load → rag_chunk → rag_embed → rag_upsert；
检索（02_condition.yaml）：rag_retrieve。向量与知识库均为确定性模拟，
不依赖真实模型与向量库。

接线约定：参数键（如 document/prompt）是稳定契约，直接按 ctx 键取；
上游节点输出则按形状识别（节点名 = ctx 键，中文命名随时可改），
与 cfg_publish 扫描 "title" 的做法同款。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.registry.core import node


def _find_upstream(ctx: dict[str, Any], shape: Callable[[Any], bool], what: str) -> Any:
    """倒序找最近一个符合形状的上游输出（找不到即中文 ValueError）。"""
    for value in reversed(list(ctx.values())):
        if shape(value):
            return value
    raise ValueError(f"上游缺少{what}")


def _document(ctx: dict[str, Any]) -> dict[str, Any]:
    """取 rag_load 的输出（形如 {doc_id, title, text}）。"""
    return _find_upstream(
        ctx,
        lambda v: isinstance(v, dict) and "doc_id" in v and "text" in v,
        "文档输出（rag_load 的 {doc_id, title, text}）",
    )


@node(label="加载文档", description="解析上传的 document（{filename, content}），输出 {doc_id, title, text}")
async def rag_load(ctx: dict[str, Any]) -> dict[str, Any]:
    """解析 params 声明的 document 输入（前端上传控件读文本后合成
    {filename, content}）。doc_id/title 取文件名去扩展名，text 为正文。
    """
    document = ctx.get("document")
    if not isinstance(document, dict):
        raise ValueError(
            "缺少上传文档：document 必须是 {filename, content}（在 YAML params 声明为必填，创建运行时提供）"
        )
    content = document.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("上传文档正文为空：document.content 必须是非空文本")
    filename = str(document.get("filename") or "上传文档")
    stem = Path(filename).stem or "document"
    return {"doc_id": stem, "title": stem, "text": content}


@node(label="切块", description="按空行把正文切成语义段（chunk 列表）")
async def rag_chunk(ctx: dict[str, Any]) -> list[str]:
    return [p.strip() for p in _document(ctx)["text"].split("\n\n") if p.strip()]


@node(label="向量化", description="为每个 chunk 生成 8 维确定性向量（内容哈希模拟，不依赖模型）")
async def rag_embed(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    doc = _document(ctx)
    chunks = _find_upstream(
        ctx,
        lambda v: isinstance(v, list) and all(isinstance(c, str) for c in v),
        "切块输出（rag_chunk 的字符串列表）",
    )
    return [
        {
            "chunk_id": f"{doc['doc_id']}-c{i}",
            "text": chunk,
            "vector": [float(sum(ord(c) * (d + 1) for c in chunk) % 997) for d in range(8)],
        }
        for i, chunk in enumerate(chunks)
    ]


@node(label="写入向量库", description="批量 upsert 向量，返回写入统计")
async def rag_upsert(ctx: dict[str, Any]) -> str:
    await asyncio.sleep(0.05)
    doc = _document(ctx)
    embeds = _find_upstream(
        ctx,
        lambda v: isinstance(v, list) and all(isinstance(e, dict) and "vector" in e for e in v),
        "向量输出（rag_embed 的 [{chunk_id, text, vector}]）",
    )
    return f"upserted {len(embeds)} chunks from {doc['doc_id']}"


# 模拟知识库：与 rag_retrieve 的演示数据同源（真实实现应为向量库检索，见 rag_embed/rag_upsert）
_MOCK_KB: list[dict[str, Any]] = [
    {
        "source": "lore-001#c0",
        "keywords": ("北境", "要塞", "长城", "纪元", "山脉"),
        "text": "北境要塞建于第二纪元，横贯大陆北端的霜脊山脉。",
    },
    {
        "source": "lore-001#c1",
        "keywords": ("堡垒", "兵力", "风哨", "寒鸦", "冬炉", "驻军"),
        "text": "要塞由风哨、寒鸦、冬炉三段堡垒群组成，常驻兵力约八千。",
    },
    {
        "source": "lore-001#c2",
        "keywords": ("长冬", "补给", "存粮", "商路", "防线"),
        "text": "每逢长冬，商路断绝，冬炉堡的存粮要支撑整条防线的补给。",
    },
]


@node(label="知识库检索", description="模拟 RAG 检索：按提示词关键词打分返回设定片段（演示骨架，未接向量库）")
async def rag_retrieve(ctx: dict[str, Any]) -> list[dict[str, str]]:
    await asyncio.sleep(0.05)
    prompt = str(ctx.get("prompt", ""))
    ranked = sorted(_MOCK_KB, key=lambda c: -sum(prompt.count(k) for k in c["keywords"]))
    return [{"source": c["source"], "text": c["text"]} for c in ranked[:2]]
