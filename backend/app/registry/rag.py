"""
RAG 演示节点 — 文档入库与知识库检索。

入库链（01_serial.yaml）：rag_load → rag_chunk → rag_embed → rag_upsert；
检索（02_condition.yaml）：rag_retrieve。向量与知识库均为确定性模拟，
不依赖真实模型与向量库。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.registry.core import node


@node(label="加载文档", description="读入一篇设定文档，输出 {doc_id, title, text}")
async def rag_load(ctx: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(0.05)
    return {
        "doc_id": "lore-001",
        "title": "北境要塞",
        "text": (
            "北境要塞建于第二纪元，横贯大陆北端的霜脊山脉。\n\n"
            "要塞由风哨、寒鸦、冬炉三段堡垒群组成，常驻兵力约八千。\n\n"
            "每逢长冬，商路断绝，冬炉堡的存粮要支撑整条防线的补给。"
        ),
    }


@node(label="切块", description="按空行把正文切成语义段（chunk 列表）")
async def rag_chunk(ctx: dict[str, Any]) -> list[str]:
    return [p.strip() for p in ctx["load"]["text"].split("\n\n") if p.strip()]


@node(label="向量化", description="为每个 chunk 生成 8 维确定性向量（内容哈希模拟，不依赖模型）")
async def rag_embed(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": f"{ctx['load']['doc_id']}-c{i}",
            "text": chunk,
            "vector": [float(sum(ord(c) * (d + 1) for c in chunk) % 997) for d in range(8)],
        }
        for i, chunk in enumerate(ctx["chunk"])
    ]


@node(label="写入向量库", description="批量 upsert 向量，返回写入统计")
async def rag_upsert(ctx: dict[str, Any]) -> str:
    await asyncio.sleep(0.05)
    return f"upserted {len(ctx['embed'])} chunks from {ctx['load']['doc_id']}"


# 模拟知识库：与 rag_load 的演示文档同源（真实实现应为向量库检索，见 rag_embed/rag_upsert）
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
