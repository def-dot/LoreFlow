"""Agent CRUD API — 挂载在 /api/v1 下的 /agents 路由组"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import UnifiedResponseRoute
from app.models.agent import AgentRecord
from app.schemas.agent import AgentCreate, AgentUpdate

router = APIRouter(prefix="/agents", route_class=UnifiedResponseRoute, tags=["agents"])


async def _name_exists(session: AsyncSession, name: str, exclude_id: int | None = None) -> bool:
    stmt = select(AgentRecord).where(AgentRecord.name == name)
    if exclude_id is not None:
        stmt = stmt.where(AgentRecord.id != exclude_id)
    result = await session.exec(stmt)
    return result.one_or_none() is not None


@router.post("", response_model=AgentRecord, status_code=201)
async def create_agent(body: AgentCreate) -> AgentRecord:
    async with AsyncSessionLocal() as session:
        if await _name_exists(session, body.name):
            raise HTTPException(status_code=409, detail=f"Agent 名称 {body.name!r} 已存在")
        agent = AgentRecord(**body.model_dump())
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent


@router.get("", response_model=list[AgentRecord])
async def list_agents() -> list[AgentRecord]:
    async with AsyncSessionLocal() as session:
        rows = (await session.exec(
            select(AgentRecord).order_by(AgentRecord.id.desc())
        )).all()
    return rows


@router.get("/{agent_id}", response_model=AgentRecord)
async def get_agent(agent_id: int) -> AgentRecord:
    async with AsyncSessionLocal() as session:
        agent = (await session.exec(
            select(AgentRecord).where(AgentRecord.id == agent_id)
        )).one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} 不存在")
    return agent


@router.put("/{agent_id}", response_model=AgentRecord)
async def update_agent(agent_id: int, body: AgentUpdate) -> AgentRecord:
    async with AsyncSessionLocal() as session:
        agent = (await session.exec(
            select(AgentRecord).where(AgentRecord.id == agent_id)
        )).one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} 不存在")
        if await _name_exists(session, body.name, exclude_id=agent_id):
            raise HTTPException(status_code=409, detail=f"Agent 名称 {body.name!r} 已存在")

        agent.sqlmodel_update(body.model_dump())
        agent.updated_at = datetime.now()

        await session.commit()
        return agent


@router.delete("/{agent_id}")
async def delete_agent(agent_id: int) -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        agent = (await session.exec(
            select(AgentRecord).where(AgentRecord.id == agent_id)
        )).one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} 不存在")
        await session.delete(agent)
        await session.commit()
    return {"deleted": agent_id}
