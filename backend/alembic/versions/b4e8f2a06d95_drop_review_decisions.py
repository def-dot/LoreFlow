"""删除 review_decisions 表（决策改为随 run 节点快照持久化）

Revision ID: b4e8f2a06d95
Revises: d2f6b8e04c71
Create Date: 2026-08-30

决策不再独立成表：approve 端点把 {approve, reason, values} 写进
runs.nodes[节点].output.decision，重跑时 approver 从快照取——
run 记录成为决策的唯一持久层，随 run 删除一并清理。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "b4e8f2a06d95"
down_revision: Union[str, None] = "d2f6b8e04c71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("review_decisions")


def downgrade() -> None:
    # 原表结构见 b98b4d88fdec（init）/ e5b7d2f4a9c1（edits 列）/
    # d2f6b8e04c71（values 改名）；决策数据已迁入 run 快照，无法完整还原
    raise NotImplementedError("决策已随 run 快照持久化，downgrade 不支持")
