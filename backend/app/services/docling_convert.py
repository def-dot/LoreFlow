"""Docling PDF 文档转换器。"""

from __future__ import annotations

import os
import sys

from docling_core.types.doc import DoclingDocument  # type: ignore[attr-defined]


def _get_converter():
    """返回 Docling DocumentConverter 单例（含 OCR / 表格 / 公式 / 代码）。"""
    # Windows 不支持 Triton，仅在该平台禁用 torch.compile
    if sys.platform == "win32":
        os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.document_timeout = 300.0
    opts.queue_max_size = 3  # 默认100，可能触发 docling-parse bad_alloc OOM

    # OCR
    opts.do_ocr = True
    opts.ocr_options = RapidOcrOptions()

    # 结构识别
    opts.do_table_structure = True
    opts.do_formula_enrichment = True
    opts.do_code_enrichment = True

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
        }
    )


def document_convert(file_path: str) -> DoclingDocument:
    """解析 PDF 文件，返回 DoclingDocument。"""
    return _get_converter().convert(file_path).document