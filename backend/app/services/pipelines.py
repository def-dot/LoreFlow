"""流水线浏览与 CRUD（pipelines/*.yaml）。

列表只做轻量解析（快、容错：单个文件坏了跳过并告警，不让整个
目录 500）；详情走 load_dag 的完整校验与图构建（mermaid/拓扑序）。
浏览不执行 dag.run()，无需 approver（human 节点的 approver 缺失只在
真正运行时报错）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import get_logger
from app.engine import RetryPolicy, load_dag
from app.engine.resolve import parse_retry
from app.engine.validate import validate_config
from app.registry import REGISTRY

logger = get_logger(__name__)

PIPELINES_DIR = settings.PIPELINES_DIR


def list_pipelines() -> list[dict[str, Any]]:
    """枚举目录下全部 .yaml；单个文件解析失败跳过并告警。"""
    entries: list[dict[str, Any]] = []
    if not PIPELINES_DIR.is_dir():
        return entries
    for path in sorted(PIPELINES_DIR.glob("*.yaml")):
        try:
            _, config = get_pipeline(path.stem)
        except Exception as exc:
            logger.warning("Skip pipeline %s: %s", path.name, exc)
            continue
        entries.append({
            "name": str(config.get("name") or path.stem),
            "description": str(config.get("description") or ""),
            "node_count": len(config.get("nodes") or {}),
            "params": config.get("inputs") or {},
        })
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
    """根据 pipeline name 获取 YAML 原文和解析后的 config。不存在 404。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"无法读取配置文件 {path!r}: {exc}") from exc
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"配置文件 {path!r} 的 YAML 无效: {exc}") from exc
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("顶层必须是映射(dict)")
    return raw, config



def detail_from_config(
    raw: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """已解析的 YAML 配置 → 详情展示数据（图、节点行、YAML 原文）。"""
    dag = load_dag(config)
    nodes_cfg = config.get("nodes") or {}
    rows: list[dict[str, Any]] = []
    for name in dag.topological_order():
        spec = nodes_cfg.get(name) or {}
        type_val = spec.get("type")
        node_type = REGISTRY.get(type_val) if type_val else None

        row: dict[str, Any] = {
            "name": name,
            "label": spec.get("label"),
            "type": type_val,
            "type_label": node_type.label if node_type else None,
            "description": spec.get("description"),
            "type_description": node_type.description if node_type else None,
            "depends_on": list(spec.get("depends_on") or []),
            "inputs": spec.get("inputs"),
            "retry": _retry_summary(parse_retry(spec.get("retry"))),
            "condition": spec.get("condition"),
        }

        rows.append(row)

    return {
        "name": dag.name,
        "description": str(config.get("description") or ""),
        "node_count": len(dag.node_names),
        "mermaid": dag.to_mermaid(),
        "source": raw,
        "nodes": rows,
        "params": config.get("inputs") or {},
    }


# ---------------------------------------------------------------------------
# Pipeline CRUD
# ---------------------------------------------------------------------------


def _parse_and_validate(definition: str) -> dict[str, Any]:
    """解析 YAML 并校验 DAG 配置；失败抛 ValueError。"""
    try:
        config = yaml.safe_load(definition)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("YAML 顶层必须是映射(dict)")
    if not config.get("name"):
        raise ValueError("YAML 必须包含 name 字段")
    errors = validate_config(config)
    if errors:
        raise ValueError("配置校验失败:\n  " + "\n  ".join(errors))
    return config


def create_pipeline(definition: str) -> str:
    """创建 pipeline 文件：校验 YAML → 写入目录。返回 name。"""
    config = _parse_and_validate(definition)
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
    dest = PIPELINES_DIR / f"{config['name']}.yaml"
    if dest.is_file():
        raise HTTPException(status_code=409, detail=f"工作流 {config['name']!r} 已存在")
    dest.write_text(definition, encoding="utf-8")
    return str(config["name"])


def update_pipeline(name: str, definition: str) -> str:
    """更新 pipeline 文件。如果 YAML name 变了，自动重命名文件。返回最终 name。"""
    filename = name + ".yaml"
    path = PIPELINES_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {name!r} 不存在")
    config = _parse_and_validate(definition)
    new_name = str(config["name"])
    new_filename = new_name + ".yaml"
    new_path = PIPELINES_DIR / new_filename
    if new_path != path:
        if new_path.is_file():
            raise HTTPException(status_code=409, detail=f"工作流 {new_name!r} 已存在")
        new_path.write_text(definition, encoding="utf-8")
        path.unlink()
        return new_name
    path.write_text(definition, encoding="utf-8")
    return name


def delete_pipeline(name: str) -> bool:
    """删除 pipeline 文件。不存在返回 False。"""
    path = PIPELINES_DIR / (name + ".yaml")
    if not path.is_file():
        return False
    path.unlink()
    return True
