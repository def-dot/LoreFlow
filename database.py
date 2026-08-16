"""
Database layer — PostgreSQL via SQLModel.

Holds the run-record model and small async helpers; main.py keeps its own
in-memory cache and calls these to persist run snapshots.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Column
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/lorerag"
)

engine = create_async_engine(DATABASE_URL)


class RunRecord(SQLModel, table=True):
    """One persisted run snapshot (nodes stored as JSON)."""

    __tablename__ = "runs"

    id: Optional[int] = Field(default=None, primary_key=True)  # 自增
    name: str = ""          # yaml 里的 name 字段（如 content_pipeline）
    config_file: str = ""   # yaml 文件名（如 pipeline.yaml）
    mermaid: str = ""       # pipeline 图源码（创建 run 时快照进记录）
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: str = "pending"  # pending/running/completed/failed/cancelled
    error: Optional[str] = None
    nodes: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


async def init() -> None:
    """Create tables (idempotent)；旧版 TEXT 主键的表一次性重建。"""
    async with engine.begin() as conn:
        if engine.sync_engine.dialect.name == "postgresql":
            row = (await conn.exec_driver_sql(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name='runs' AND column_name='id'"
            )).fetchone()
            if row and row[0] != "integer":  # 旧 TEXT id 无法转换——重建（丢弃旧数据）
                await conn.exec_driver_sql("DROP TABLE runs")
        await conn.run_sync(SQLModel.metadata.create_all)


async def save(record: RunRecord) -> None:
    """把记录同步到库（原地，不换对象）。

    首次插入（id is None）走 add：自增 id 回填到同一对象，这样提前
    捕获了该对象的闭包（如 approver/sink）也能看到 id；之后走 merge。
    """
    async with AsyncSession(engine) as session:
        if record.id is None:
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return
        await session.merge(record)
        await session.commit()


async def load() -> Dict[int, RunRecord]:
    """All persisted run records, keyed by run id."""
    async with AsyncSession(engine) as session:
        records = (await session.exec(select(RunRecord))).all()
    return {rec.id: rec for rec in records}


async def get(run_id: int) -> Optional[RunRecord]:
    """One run record, or ``None``."""
    async with AsyncSession(engine) as session:
        return await session.get(RunRecord, run_id)


class ReviewDecision(SQLModel, table=True):
    """一条人工审批决策——审批器挂起时轮询这张表取决策。"""

    __tablename__ = "review_decisions"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int
    node_name: str
    approve: bool
    reason: Optional[str] = None
    created_at: Optional[str] = None


async def save_decision(run_id: int, node_name: str, decision: Dict[str, Any]) -> None:
    """写入一条审批决策（浏览器答复）。"""
    row = ReviewDecision(
        run_id=run_id,
        node_name=node_name,
        approve=decision["approve"],
        reason=decision.get("reason"),
        created_at=datetime.now().isoformat(timespec="seconds"),
    )
    async with AsyncSession(engine) as session:
        session.add(row)
        await session.commit()


async def take_decision(run_id: int, node_name: str) -> Optional[Dict[str, Any]]:
    """取走并删除该节点最近一条决策（审批器轮询消费）。"""
    async with AsyncSession(engine) as session:
        result = await session.exec(
            select(ReviewDecision)
            .where(ReviewDecision.run_id == run_id, ReviewDecision.node_name == node_name)
            .order_by(ReviewDecision.id.desc())
        )
        row = result.first()
        if row is None:
            return None
        decision = {"approve": row.approve, "reason": row.reason}
        await session.delete(row)
        await session.commit()
        return decision
