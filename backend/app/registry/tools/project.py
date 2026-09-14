"""项目助手 Agent 工具 — 通用数据库查询。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import Column
from sqlalchemy import cast, String

from app.core.database import AsyncSessionLocal
from app.models.agent import AgentRecord, ConversationRecord, MessageRecord
from app.models.run import RunRecord
from app.registry.tool import tool

logger = logging.getLogger(__name__)

_MODELS: dict[str, type] = {
    "agents": AgentRecord,
    "conversations": ConversationRecord,
    "messages": MessageRecord,
    "runs": RunRecord,
}


def _serialize_row(row: Any) -> dict[str, Any]:
    """SQLModel 行 → 可序列化字典，处理 datetime。"""
    data = {}
    for k, v in row.__dict__.items():
        if k.startswith("_"):
            continue
        if isinstance(v, datetime):
            data[k] = v.isoformat()
        else:
            data[k] = v
    return data


@tool(
    name="query_database",
    description=(
        "通用数据库查询工具。可查询 agents、conversations、messages、runs 四张表。"
        "filters 格式：field1=value1|field2=value2（AND 关系，仅支持等值匹配）。"
    ),
    params={
        "table": "表名：agents / conversations / messages / runs",
        "filters": "筛选条件，如 status=running 或 agent_id=1|status=completed",
        "order_by": "排序字段，默认 id",
        "limit": "返回条数，默认 20",
    },
)
async def query_database_tool(
    table: str,
    filters: str = "",
    order_by: str = "id",
    limit: int = 20,
) -> str:
    model = _MODELS.get(table)
    if model is None:
        return f"未知表：{table}，可查询：{', '.join(_MODELS)}"

    from sqlmodel import select

    stmt = select(model)

    # 解析筛选条件
    if filters:
        for pair in filters.split("|"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            field, _, value = pair.partition("=")
            field = field.strip()
            value = value.strip()
            col: Column | None = getattr(model, field, None)
            if col is None:
                return f"未知字段：{field}（表 {table}）"
            stmt = stmt.where(cast(col, String) == value)

    # 排序
    order_col = getattr(model, order_by, None)
    if order_col is not None:
        stmt = stmt.order_by(order_col.desc() if order_by == "id" else order_col)

    stmt = stmt.limit(limit)

    async with AsyncSessionLocal() as session:
        rows = (await session.exec(stmt)).all()

    if not rows:
        return "未找到匹配记录。"

    result = [_serialize_row(r) for r in rows]
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)
