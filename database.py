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
    name: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    running: bool = False
    result: str = "pending"
    error: Optional[str] = None
    nodes: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    pending: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


async def init() -> None:
    """Create tables (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


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
