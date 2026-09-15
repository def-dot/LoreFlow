"""
RAG 演示节点 — 文档入库与知识库检索。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.registry.types import func
from app.utils import files


@func(
    label="加载文档",
    description="读取上传文档内容",
    metadata={"group": "RAG", "order": 10},
    params={"document": "上传文档"},
    output_schema={
        "type": "object",
        "fields": {
            "doc_name": {"type": "string", "description": "文档名称"},
            "text": {"type": "string", "description": "文档正文"},
        },
    },
)
async def rag_load(document: dict) -> dict[str, Any]:
    """从上传目录读取 params 声明的 document 文件"""
    if not isinstance(document, dict):
        raise ValueError("缺少上传文档：document 必须是 {id, filename} 字段")
    upload_id = document.get("id")
    if not isinstance(upload_id, str) or not upload_id.strip():
        raise ValueError("上传文档缺少文件id字段：document.id")
    text = files.read_upload(upload_id)  # 路径穿越/扩展名非法/文件缺失 → 中文 ValueError
    if not text.strip():
        raise ValueError("上传文档正文为空：文件内容为空白文本")
    filename = str(document.get("filename") or "上传文档")
    stem = Path(filename).stem or "document"
    return {"doc_name": stem, "text": text}


@func(
    label="切块",
    description="按空行把正文切成语义段",
    metadata={"group": "RAG", "order": 20},
    params={"text": "文档正文"},
    output_schema={"type": "list", "item": {"type": "string"}, "description": "文本段"},
)
async def rag_chunk(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("缺少文档正文字段text")
    return [p.strip() for p in text.split("\r\n\r\n") if p.strip()]


@func(
    label="向量化",
    description="为每个 chunk 生成向量",
    metadata={"group": "RAG", "order": 30},
    params={"doc_name": "文档名称", "chunks": "文本段列表"},
    output_schema={
        "type": "list",
        "item": {
            "type": "object",
            "fields": {
                "chunk_id": {"type": "string", "description": "块标识"},
                "text": {"type": "string", "description": "块文本"},
                "vector": {"type": "list", "item": {"type": "float"}, "description": "浮点向量"},
            },
        },
    },
)
async def rag_embed(doc_name: str, chunks: list) -> list[dict[str, Any]]:
    if not isinstance(doc_name, str) or not doc_name.strip():
        raise ValueError("缺少文档名称字段doc_name")
    if not isinstance(chunks, list):
        raise ValueError("缺少切块信息字段chunks")
    return [
        {
            "chunk_id": f"{doc_name}-c{i}",
            "text": chunk,
            "vector": [float(sum(ord(c) * (d + 1) for c in chunk) % 997) for d in range(8)],
        }
        for i, chunk in enumerate(chunks)
    ]


@func(
    label="写入向量库",
    description="批量写入向量信息",
    metadata={"group": "RAG", "order": 40},
    params={"doc_name": "文档名称", "embeds": "向量列表"},
    output_schema={"type": "string", "description": "成功写库的向量数量统计"},
)
async def rag_upsert(doc_name: str = "", embeds: list | None = None) -> str:
    await asyncio.sleep(0.05)
    if not isinstance(doc_name, str) or not doc_name.strip():
        raise ValueError("缺少文档名称字段doc_name")
    if not isinstance(embeds, list):
        raise ValueError("缺少向量输出字段embeds")
    return f"upserted {len(embeds)} chunks from {doc_name}"


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


@func(
    label="知识库检索",
    description="关键词查询返回片段",
    metadata={"group": "RAG", "order": 50},
    params={"prompt": "检索关键词"},
    output_schema={
        "type": "list",
        "item": {
            "type": "object",
            "fields": {
                "source": {"type": "string", "description": "片段来源标识"},
                "text": {"type": "string", "description": "片段正文"},
            },
        },
    },
)
async def rag_retrieve(prompt: str = "") -> list[dict[str, str]]:
    await asyncio.sleep(0.05)
    ranked = sorted(_MOCK_KB, key=lambda c: -sum(str(prompt).count(k) for k in c["keywords"]))
    return [{"source": c["source"], "text": c["text"]} for c in ranked[:2]]
