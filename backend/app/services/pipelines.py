"""Demo 流水线只读浏览 — 枚举 app/pipelines/*.yaml 并合成列表/详情。

列表只做轻量解析（快、容错：单个文件坏了跳过并告警，不让整个
目录 500）；详情走 load_dag 的完整校验与图构建（mermaid/拓扑序）。
approver 必须传 no-op：含 human 节点的流水线在构建时会校验 approver
存在，但浏览不执行 dag.run()，no-op 永远不会被调用。
"""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import HTTPException

from app.core.config import settings
from app.core.logging import get_logger
from app.engine import RetryPolicy, load_dag
from app.engine.declarative import read_yaml
from app.engine.resolve import parse_retry
from app.engine.validate import validate_config
from app.models.run import RunRecord
from app.registry import REGISTRY

logger = get_logger(__name__)


def _param_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    """已校验的顶层 ``inputs`` 声明 → 前端参数行（必填/默认值由行内字段判断）。

    对外响应字段沿用 ``params``（前端契约不变），数据源是 YAML 的 ``inputs``。
    """
    return [
        {
            "name": name,
            "label": spec.get("label") or name,
            "description": spec.get("description"),
            "default": spec.get("default"),
            "has_default": "default" in spec,
            "required": bool(spec.get("required")),
            "multiline": bool(spec.get("multiline", False)),
            "file": bool(spec.get("file", False)),
        }
        for name, spec in (config.get("inputs") or {}).items()
    ]


async def _noop_approver(node_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """静态浏览用的空审批器：load_dag 要求 human 节点有 approver，但浏览不执行。"""
    return {"approve": False}


def list_pipelines() -> list[dict[str, Any]]:
    """枚举 pipelines 目录全部 .yaml；单个文件解析失败跳过并告警。"""
    entries: list[dict[str, Any]] = []
    for path in sorted(settings.PIPELINES_DIR.glob("*.yaml")):
        try:
            _, config = read_yaml(path)
        except ValueError as exc:
            logger.warning("Skip demo pipeline %s: %s", path.name, exc)
            continue
        # 与 load_dag 同源校验（坏声明跳过并告警，一次报全部错误）
        errors = validate_config(config)
        if errors:
            logger.warning("Skip demo pipeline %s: %s", path.name, "; ".join(errors))
            continue
        entries.append({
            "filename": path.name,
            "name": str(config.get("name") or path.stem),
            "description": str(config.get("description") or ""),
            "node_count": len(config.get("nodes") or {}),
            "params": _param_rows(config),
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


def _node_row(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """把 YAML 节点 spec 合成展示行：类型名来自 spec，label/描述来自注册表。

    human/loop 是引擎内置 kind，不在注册表里，给固定 label；type_description
    分别放审核提示和循环体摘要。
    """
    kind = spec.get("kind", "node")
    cond_spec = spec.get("condition")
    row: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "type": None,
        "type_label": None,
        "type_description": None,
        "depends_on": list(spec.get("depends_on") or []),
        "retry": _retry_summary(parse_retry(spec.get("retry"))),
        "condition": cond_spec,
        "condition_label": None,
        "review": None,
    }
    if kind == "node":
        row["type"] = spec.get("type")
        node_type = REGISTRY.get(row["type"]) if row["type"] else None
        if node_type:
            row["type_label"] = node_type.label
            row["type_description"] = node_type.description
    elif kind == "human":
        row["type_label"] = "人工审核"
        row["type_description"] = spec.get("prompt")
        # 原始富声明 {key: {label}} 直通前端；格式已由 load_dag 校验
        row["review"] = spec.get("review")
    elif kind == "loop":
        row["type_label"] = "循环"
        body = spec.get("body") or {}
        row["type_description"] = f"循环体 {len(body)} 个节点，上限 {spec.get('max_iterations', 100)} 轮"
    if isinstance(cond_spec, bool):
        row["condition_label"] = "恒执行" if cond_spec else "恒跳过"
    elif cond_spec:
        row["condition_label"] = cond_spec  # 表达式原文即展示标签
    return row


def get_pipeline_detail(filename: str) -> dict[str, Any]:
    """单个流水线的完整展示数据：图、节点行、YAML 原文；未知文件名 404。"""
    path = settings.PIPELINES_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"流水线 {filename!r} 不存在")
    raw, config = read_yaml(path)
    return _detail_from_config(path.name, raw, config)


def get_run_definition_detail(record: RunRecord) -> dict[str, Any]:
    """run 钉住的 definition 快照 → 展示数据（与 get_pipeline_detail 同构）。

    run 的「查看配置」必须看创建时快照：当前文件在 run 创建后可能已被
    修改甚至删除，按文件名查看到的不是这次运行实际执行的定义。
    """
    config = yaml.safe_load(record.definition)
    if not isinstance(config, dict):
        raise HTTPException(status_code=500, detail="run 配置快照解析失败：顶层不是映射")
    return _detail_from_config(record.config_file, record.definition, config)


def mermaid_from_definition(record: RunRecord) -> str | None:
    """definition 快照 → 当前渲染格式的 mermaid；解析/构建失败返回 None。

    存储的 record.mermaid 是创建时刻的渲染快照——渲染格式改进后存量
    run 不会跟上；definition 本身钉住不变，读取时现渲染既忠实又始终
    是最新格式。失败（旧记录无快照、注册表缺类型等）由调用方回退存储值。
    """
    if not record.definition:
        return None
    try:
        config = yaml.safe_load(record.definition)
        if not isinstance(config, dict):
            return None
        return load_dag(config, approver=_noop_approver).to_mermaid()
    except Exception as exc:
        logger.warning("[run %s] definition 现渲染 mermaid 失败，回退存储快照: %s", record.id, exc)
        return None


def _detail_from_config(filename: str, raw: str, config: dict[str, Any]) -> dict[str, Any]:
    """已解析的 YAML 配置 → 详情展示数据（图、节点行、YAML 原文）。"""
    dag = load_dag(config, approver=_noop_approver)
    nodes_cfg = config.get("nodes") or {}
    rows = [_node_row(name, nodes_cfg.get(name) or {}) for name in dag.topological_order()]
    return {
        "filename": filename,
        "name": dag.name,
        "description": str(config.get("description") or ""),
        "node_count": len(dag.node_names),
        "mermaid": dag.to_mermaid(),
        "source": raw,
        "nodes": rows,
        "params": _param_rows(config),
    }
