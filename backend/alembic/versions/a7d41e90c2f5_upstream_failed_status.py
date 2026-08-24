"""upstream_failed becomes its own node status

Revision ID: a7d41e90c2f5
Revises: c41f9a2d7e03
Create Date: 2026-08-24 00:00:00.000000

节点快照（runs.nodes JSON）中原以 status="skipped" + skip_reason=
"upstream_failed" 表示的失败级联，改为独立状态 status="upstream_failed"，
skip_reason 字段整个移除（skipped 从此只表示条件不满足的分支跳过）。
纯数据迁移，无表结构变更。
"""
import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7d41e90c2f5'
down_revision: str | None = 'c41f9a2d7e03'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rewrite(nodes: dict, upgrade: bool) -> dict | None:
    """就地改写一份节点快照；无变化返回 None（跳过 UPDATE）。"""
    changed = False
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        if upgrade:
            if (
                entry.get("status") == "skipped"
                and entry.get("skip_reason") == "upstream_failed"
            ):
                entry["status"] = "upstream_failed"
                changed = True
            if "skip_reason" in entry:  # 条件跳过等原因字段一并清除
                del entry["skip_reason"]
                changed = True
        else:
            if entry.get("status") == "upstream_failed":
                entry["status"] = "skipped"
                entry["skip_reason"] = "upstream_failed"
                changed = True
    return nodes if changed else None


def _migrate(direction: str) -> None:
    conn = op.get_bind()
    # sa.JSON 绑定按方言序列化（Postgres CAST / SQLite TEXT），避免手写 CAST
    runs_tbl = sa.table("runs", sa.column("id", sa.Integer), sa.column("nodes", sa.JSON))
    rows = conn.execute(sa.text("SELECT id, nodes FROM runs")).fetchall()
    for row_id, nodes in rows:
        if isinstance(nodes, str):  # SQLite 等 driver 返回 JSON 字符串
            nodes = json.loads(nodes)
        if not isinstance(nodes, dict):
            continue
        rewritten = _rewrite(nodes, direction == "upgrade")
        if rewritten is None:
            continue
        conn.execute(runs_tbl.update().where(runs_tbl.c.id == row_id).values(nodes=rewritten))


def upgrade() -> None:
    _migrate("upgrade")


def downgrade() -> None:
    _migrate("downgrade")
