"""Orchestrator 恢复语义 — 定义快照钉住（_dag_for_resume）。

挂起期间工作流文件可能被改：恢复永远按创建时钉住的 definition 构建
DAG（漂移仅告警）；存量旧行无快照时回退当前文件（历史行为）。
"""

import logging

from app.core.config import settings
from app.models.run import RunRecord
from app.services.orchestrator import _dag_for_resume

#: 与仓库内任何流水线都不同构的最小合法定义
_PINNED = """
name: pinned_demo
nodes:
  data:
    type: cfg_fetch
  extra:
    type: cfg_fetch
    depends_on: [data]
"""


def _record(config_file: str, definition: str | None) -> RunRecord:
    return RunRecord(name="t", config_file=config_file, definition=definition)


def test_resume_prefers_pinned_definition() -> None:
    """有快照：按 definition 构建 DAG，config_file 指向的文件不存在也不阻断。"""
    record = _record("gone_after_suspend.yaml", _PINNED)

    dag = _dag_for_resume(record)

    assert dag.name == "pinned_demo"
    assert set(dag.nodes) == {"data", "extra"}


def test_resume_warns_on_drift_but_stays_pinned(caplog) -> None:
    """文件与快照不一致：告警一次，DAG 仍来自快照而非当前文件。"""
    record = _record("05_human_review.yaml", _PINNED)

    with caplog.at_level(logging.WARNING, logger="app.services.orchestrator"):
        dag = _dag_for_resume(record)

    assert any("按创建时钉住的定义续跑" in r.getMessage() for r in caplog.records)
    assert dag.name == "pinned_demo"  # 来自快照，不是磁盘上的 05 流水线


def test_resume_falls_back_to_file_without_snapshot() -> None:
    """存量旧行 definition 为 NULL：回退读当前文件（升级前的行为不变）。"""
    record = _record("05_human_review.yaml", None)

    dag = _dag_for_resume(record)

    assert set(dag.nodes) >= {"review", "publish"}  # 05 流水线的节点
