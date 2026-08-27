"""
RAG 演示节点 — 文档入库与知识库检索。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.registry.core import node
from app.utils import files


@node(label="加载文档", description="按 document（{id, filename}）引用读取上传文件，输出 {doc_id, title, text}")
async def rag_load(ctx: dict[str, Any]) -> dict[str, Any]:
    """从上传目录读取 params 声明的 document 文件
    """
    document = ctx.get("document")
    if not isinstance(document, dict):
        raise ValueError(
            "缺少上传文档：document 必须是 {id, filename} 引用（在 YAML inputs 声明为必填，创建运行时提供）"
        )
    upload_id = document.get("id")
    if not isinstance(upload_id, str) or not upload_id.strip():
        raise ValueError("上传文档缺少文件引用：document.id 必须是上传接口返回的 id")
    text = files.read_upload(upload_id)  # 路径穿越/扩展名非法/文件缺失 → 中文 ValueError
    if not text.strip():
        raise ValueError("上传文档正文为空：文件内容为空白文本")
    filename = str(document.get("filename") or "上传文档")
    stem = Path(filename).stem or "document"
    return {"doc_id": stem, "title": stem, "text": text}


@node(label="切块", description="按空行把正文切成语义段；输入 document ← rag_load 节点（YAML inputs 接线）")
async def rag_chunk(ctx: dict[str, Any]) -> list[str]:
    document = ctx.get("document")
    if not isinstance(document, dict):
        raise ValueError("上游缺少文档输出：inputs 需接线 document ← rag_load 节点（或该上游被条件跳过）")
    return [p.strip() for p in document["text"].split("\n\n") if p.strip()]


@node(label="向量化", description="为每个 chunk 生成 8 维确定性向量；输入 document、chunks ← rag_load/rag_chunk 节点（YAML inputs 接线）")
async def rag_embed(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    document = ctx.get("document")
    if not isinstance(document, dict):
        raise ValueError("上游缺少文档输出：inputs 需接线 document ← rag_load 节点（或该上游被条件跳过）")
    chunks = ctx.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("上游缺少切块输出：inputs 需接线 chunks ← rag_chunk 节点（或该上游被条件跳过）")
    return [
        {
            "chunk_id": f"{document['doc_id']}-c{i}",
            "text": chunk,
            "vector": [float(sum(ord(c) * (d + 1) for c in chunk) % 997) for d in range(8)],
        }
        for i, chunk in enumerate(chunks)
    ]


@node(label="写入向量库", description="批量 upsert 向量，返回写入统计；输入 document、embeds ← rag_load/rag_embed 节点（YAML inputs 接线）")
async def rag_upsert(ctx: dict[str, Any]) -> str:
    await asyncio.sleep(0.05)
    document = ctx.get("document")
    if not isinstance(document, dict):
        raise ValueError("上游缺少文档输出：inputs 需接线 document ← rag_load 节点（或该上游被条件跳过）")
    embeds = ctx.get("embeds")
    if not isinstance(embeds, list):
        raise ValueError("上游缺少向量输出：inputs 需接线 embeds ← rag_embed 节点（或该上游被条件跳过）")
    return f"upserted {len(embeds)} chunks from {document['doc_id']}"


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

