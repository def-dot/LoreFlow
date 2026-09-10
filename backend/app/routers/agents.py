"""Agent CRUD API — 挂载在 /api/v1 下的 /agents 路由组"""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import UnifiedResponseRoute
from app.models.agent import AgentRecord

router = APIRouter(prefix="/agents", route_class=UnifiedResponseRoute, tags=["agents"])


@router.post("", response_model=AgentRecord, status_code=201)
async def create_agent(body: AgentRecord) -> AgentRecord:
    async with AsyncSessionLocal() as session:
        session.add(body)
        await session.commit()
        await session.refresh(body)
        return body


@router.get("", response_model=list[AgentRecord])
async def list_agents() -> list[AgentRecord]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentRecord).order_by(AgentRecord.id.desc())
        )
        rows = result.scalars().all()
    return rows


@router.get("/{agent_id}", response_model=AgentRecord)
async def get_agent(agent_id: int) -> AgentRecord:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentRecord).where(AgentRecord.id == agent_id)
        )
        agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} 不存在")
    return agent


@router.put("/{agent_id}", response_model=AgentRecord)
async def update_agent(agent_id: int, body: AgentRecord) -> AgentRecord:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentRecord).where(AgentRecord.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} 不存在")

        update_data = body.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(agent, key, value)
        agent.updated_at = datetime.now()

        await session.commit()
        await session.refresh(agent)
        return agent


@router.delete("/{agent_id}")
async def delete_agent(agent_id: int) -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentRecord).where(AgentRecord.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id!r} 不存在")
        await session.delete(agent)
        await session.commit()
    return {"deleted": agent_id}
