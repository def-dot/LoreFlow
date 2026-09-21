"""流水线浏览与 CRUD（pipelines/*.yaml）。

列表只做轻量解析（快、容错：单个文件坏了跳过并告警，不让整个
目录 500）；详情直接遍历 Pipeline + REGISTRY，不构建 DAG。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import get_logger
from app.engine import RetryPolicy

from app.engine.pipeline import Node, Pipeline
from app.registry import REGISTRY

logger = get_logger(__name__)

PIPELINES_DIR = settings.PIPELINES_DIR


def list_pipelines() -> list[Pipeline]:
    """枚举目录下全部 .yaml；单个文件解析失败跳过并告警。"""
    entries: list[Pipeline] = []
    for path in sorted(PIPELINES_DIR.glob("*.yaml")):
        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
            cfg = Pipeline.model_validate(data)
        except Exception as exc:
            logger.warning("Skip pipeline %s: %s", path.name, exc)
            continue
        entries.append(cfg)
    return entries


def _retry_summary(rp: RetryPolicy | None) -> str | None:
    """RetryPolicy → 中文摘要；默认值不展示，max_retries=0 即不重试。"""
    if rp is None:
        return None
    if rp.max_retries == 0:
        return "不重试"
    parts = [f"重试 {rp.max_retries} 次"]
    if rp.backoff_base != 1.0 or rp.backoff_factor != 2.0 or rp.backoff_max != 60.0:
        parts.append(f"退避 {rp.backoff_base:g}s×{rp.backoff_factor:g}（≤{rp.backoff_max:g}s）")
    if rp.retry_on and rp.retry_on != (Exception,):
        parts.append("仅 " + "、".join(c.__name__ for c in rp.retry_on))
    if not rp.jitter:
        parts.append("无抖动")
    return "，".join(parts)




def get_pipeline(name: str) -> tuple[str, dict[str, Any]]:
    """根据 pipeline name 获取 YAML 原文和解析后的 config dict。不存在 404。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    cfg = Pipeline.model_validate(data)
    return raw, cfg.model_dump()


def _topo_sort(nodes: list[Node]) -> list[str]:
    """Kahn 算法拓扑排序（仅用于展示顺序）。"""
    names = [n.name for n in nodes]
    in_degree: dict[str, int] = {n: 0 for n in names}
    dependents: dict[str, list[str]] = {n: [] for n in names}
    for node in nodes:
        for dep in node.depends_on:
            if dep in dependents:
                dependents[dep].append(node.name)
                in_degree[node.name] += 1
    queue = [n for n, d in in_degree.items() if d == 0]
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for downstream in dependents[n]:
            in_degree[downstream] -= 1
            if in_degree[downstream] == 0:
                queue.append(downstream)
    return order


def _to_mermaid(cfg: Pipeline) -> str:
    """直接从配置生成 Mermaid 流程图（不经过 DAG）。"""
    lines = ["graph TD"]
    for node_cfg in cfg.nodes:
        name = node_cfg.name
        nid = name.replace(" ", "_").replace("-", "_")
        node_type = REGISTRY.get(node_cfg.type)
        main_text = node_cfg.label or name
        small: list[str] = []
        if node_type and node_type.label:
            small.append(node_type.label)
        if node_cfg.condition:
            small.append("[?]")
        rp = node_cfg.retry
        if isinstance(rp, RetryPolicy) and rp.max_retries:
            small.append(f"[R{rp.max_retries}]")
        text = main_text + (f"<br/><i>{' '.join(small)}</i>" if small else "")
        lines.append(f'    {nid}["{text}"]')
        for dep in node_cfg.depends_on:
            did = dep.replace(" ", "_").replace("-", "_")
            lines.append(f"    {did} --> {nid}")
    return "\n".join(lines)


def detail_from_config(
    raw: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """已解析的 YAML 配置 → 详情展示数据（图、节点行、YAML 原文）。"""
    cfg = Pipeline.model_validate(config)
    by_name = cfg.node_map
    rows: list[dict[str, Any]] = []
    for name in _topo_sort(cfg.nodes):
        node_cfg = by_name[name]
        node_type = REGISTRY.get(node_cfg.type)
        rows.append({
            "name": name,
            "label": node_cfg.label or None,
            "type": node_cfg.type,
            "type_label": node_type.label if node_type else None,
            "description": node_cfg.description,
            "type_description": node_type.description if node_type else None,
            "type_input_schema": node_type.input_schema.model_json_schema() if node_type and node_type.input_schema else None,
            "type_output_schema": node_type.output_schema.model_json_schema() if node_type and node_type.output_schema else None,
            "depends_on": list(node_cfg.depends_on),
            "inputs": node_cfg.inputs,
            "retry": _retry_summary(node_cfg.retry),
            "condition": node_cfg.condition,
        })

    return {
        "name": cfg.name or "",
        "description": cfg.description or "",
        "node_count": len(cfg.nodes),
        "mermaid": _to_mermaid(cfg),
        "source": raw,
        "nodes": rows,
    }


# ---------------------------------------------------------------------------
# Pipeline CRUD
# ---------------------------------------------------------------------------


def create_pipeline(definition: str) -> Pipeline:
    """创建 pipeline 文件：校验 YAML → 写入目录。"""
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    dest = PIPELINES_DIR / f"{cfg.name}.yaml"
    if dest.is_file():
        raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
    dest.write_text(definition, encoding="utf-8")
    return cfg


def update_pipeline(name: str, definition: str) -> Pipeline:
    """更新 pipeline 文件。如果 YAML name 变了，自动重命名文件。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
    try:
        data = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    cfg = Pipeline.model_validate(data)
    new_path = PIPELINES_DIR / f"{cfg.name}.yaml"
    if new_path != path:
        if new_path.is_file():
            raise HTTPException(status_code=409, detail=f"工作流 {cfg.name!r} 已存在")
        new_path.write_text(definition, encoding="utf-8")
        path.unlink()
        return cfg
    path.write_text(definition, encoding="utf-8")
    return cfg


def delete_pipeline(name: str) -> bool:
    """删除 pipeline 文件。不存在返回 False。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        return False
    path.unlink()
    return True
