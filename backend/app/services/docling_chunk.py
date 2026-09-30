"""文档智能切片器 — Docling + HybridChunker。"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from docling_core.types.doc import DoclingDocument  # type: ignore[attr-defined]

from app.services.docling_convert import document_convert


@lru_cache(maxsize=1)
def _get_chunker():
    """返回 HybridChunker 单例（BGE-M3 tokenizer, max_tokens=512）。"""
    from transformers import AutoTokenizer

    from docling.chunking import HybridChunker  # type: ignore[attr-defined]
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer

    bge_tok = AutoTokenizer.from_pretrained("./bge-m3-tokenizer")
    tokenizer = HuggingFaceTokenizer(tokenizer=bge_tok, max_tokens=512)
    return HybridChunker(tokenizer=tokenizer)


def document_chunk(file_path: str, doc_id: int, *, doc: DoclingDocument = None) -> list[dict[str, Any]]:
    """将文档切片为带元数据的文本块。可传入已转换的 doc 避免重复解析。"""
    if doc is None:
        doc = document_convert(file_path)

    # 保存解析结果供后续按页查看
    debug_dir = Path("parsed")
    debug_dir.mkdir(exist_ok=True)
    doc.save_as_json(debug_dir / f"{doc_id}.json")

    doc_chunks = list(_get_chunker().chunk(doc))

    source_file = os.path.basename(file_path)
    formatted: list[dict[str, Any]] = []
    for i, chunk in enumerate(doc_chunks):
        text = chunk.text.replace("\x00 ", "-")

        headings = getattr(chunk.meta, "headings", []) or []
        heading_ctx = (
            " > ".join(h if isinstance(h, str) else h.text for h in headings)
            if headings
            else "Root"
        )
        pages = list(getattr(chunk.meta, "page_numbers", [])) or [1]

        formatted.append(
            {
                "enriched_text": f"[章节上下文: {heading_ctx}]\n{text}",
                "raw_text": text,
                "metadata": {
                    "source_file": source_file,
                    "chunk_id": i,
                    "heading_context": heading_ctx,
                    "page_numbers": pages,
                },
            }
        )
    return formatted


if __name__ == "__main__":
    import argparse
    import traceback

    parser = argparse.ArgumentParser()
    parser.add_argument("file_path")
    parser.add_argument("--output", required=True)
    parser.add_argument("--doc-id", type=int, default=0)
    args = parser.parse_args()

    result: dict[str, Any] = {"ok": False, "error": ""}
    try:
        chunks = document_chunk(args.file_path, args.doc_id)
        result = {"ok": True, "data": chunks}
    except Exception as exc:
        result = {"ok": False, "error": f"{exc}\n{traceback.format_exc()}"}

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)