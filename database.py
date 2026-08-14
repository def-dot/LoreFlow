"""
Database layer — PostgreSQL via SQLModel.

Holds the run-record model and small async helpers; main.py keeps its own
in-memory cache and calls these to persist run snapshots.
"""

from __future__ import annotations

import os
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
    """One persisted run snapshot (nodes/pending stored as JSON)."""

    __tablename__ = "runs"

    id: str = Field(primary_key=True)
    name: str = ""          # yaml 里的 name 字段（如 content_pipeline）
    config_file: str = ""   # yaml 文件名（如 pipeline.yaml）
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: str = "pending"  # pending/running/completed/failed/cancelled
    error: Optional[str] = None
    nodes: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


async def init() -> None:
    """Create tables (idempotent)；开发期为旧表做一次性列迁移。"""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        if engine.sync_engine.dialect.name == "postgresql":
            await conn.exec_driver_sql(
                "ALTER TABLE runs ADD COLUMN IF NOT EXISTS config_file TEXT NOT NULL DEFAULT ''"
            )
            cols = {
                row[0] for row in (await conn.exec_driver_sql(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='runs'"
                )).fetchall()
            }
            if "result" in cols:  # result -> status 重命名（一次性）
                await conn.exec_driver_sql("ALTER TABLE runs RENAME COLUMN result TO status")
            await conn.exec_driver_sql("ALTER TABLE runs DROP COLUMN IF EXISTS running")
            # interrupted -> cancelled 术语统一（一次性，幂等）
            await conn.exec_driver_sql(
                "UPDATE runs SET status='cancelled' WHERE status='interrupted'"
            )
            # 待审是瞬态信息，无需持久化
            await conn.exec_driver_sql("ALTER TABLE runs DROP COLUMN IF EXISTS pending")


async def save(record: RunRecord) -> None:
    """Upsert one run record."""
    async with AsyncSession(engine) as session:
        await session.merge(record)
        await session.commit()


async def load() -> Dict[str, RunRecord]:
    """All persisted run records, keyed by run id."""
    async with AsyncSession(engine) as session:
        records = (await session.exec(select(RunRecord))).all()
    return {rec.id: rec for rec in records}


async def get(run_id: str) -> Optional[RunRecord]:
    """One run record, or ``None``."""
    async with AsyncSession(engine) as session:
        return await session.get(RunRecord, run_id)
