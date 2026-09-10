"""对话 API — 挂载在 /api/v1 下的 /conversations 路由组"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import AsyncSessionLocal
from app.core.response import UnifiedResponseRoute
from app.models.agent import AgentRecord, ConversationRecord, MessageRecord
from app.schemas.agents import (
    ChatRequest,
    ConversationCreate,
    ConversationDetail,
    ConversationListItem,
    MessageItem,
)
from app.services.agent_chat import run_agent_chat

router = APIRouter(prefix="/conversations", route_class=UnifiedResponseRoute, tags=["conversations"])


@router.post("", response_model=ConversationListItem, status_code=201)
async def create_conversation(body: ConversationCreate) -> ConversationListItem:
    # 验证 agent 存在
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentRecord).where(AgentRecord.id == body.agent_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"Agent {body.agent_id!r} 不存在")

        conv = ConversationRecord(
            agent_id=body.agent_id,
            title=body.title,
        )
        session.add(conv)
        await session.commit()
        await session.refresh(conv)
        return ConversationListItem.model_validate(conv)


@router.get("", response_model=list[ConversationListItem])
async def list_conversations(
    agent_id: int | None = Query(None, description="按 Agent 筛选"),
) -> list[ConversationListItem]:
    async with AsyncSessionLocal() as session:
        stmt = select(ConversationRecord).order_by(ConversationRecord.id.desc())
        if agent_id is not None:
            stmt = stmt.where(ConversationRecord.agent_id == agent_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return [ConversationListItem.model_validate(r) for r in rows]


@router.get("/{conv_id}", response_model=ConversationDetail)
async def get_conversation(conv_id: int) -> ConversationDetail:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationRecord).where(ConversationRecord.id == conv_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail=f"对话 {conv_id!r} 不存在")

        msg_result = await session.execute(
            select(MessageRecord)
            .where(MessageRecord.conversation_id == conv_id)
            .order_by(MessageRecord.id)
        )
        messages = msg_result.scalars().all()

    return ConversationDetail(
        **ConversationListItem.model_validate(conv).model_dump(),
        messages=[MessageItem.model_validate(m) for m in messages],
    )


@router.delete("/{conv_id}")
async def delete_conversation(conv_id: int) -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationRecord).where(ConversationRecord.id == conv_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail=f"对话 {conv_id!r} 不存在")

        # 先删除关联消息
        msg_result = await session.execute(
            select(MessageRecord).where(MessageRecord.conversation_id == conv_id)
        )
        for msg in msg_result.scalars().all():
            await session.delete(msg)

        await session.delete(conv)
        await session.commit()
    return {"deleted": conv_id}


@router.post("/{conv_id}/chat")
async def chat(conv_id: int, body: ChatRequest):
    """发送消息，返回 SSE 流式响应。"""
    # 验证对话和 Agent 存在
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ConversationRecord).where(ConversationRecord.id == conv_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            raise HTTPException(status_code=404, detail=f"对话 {conv_id!r} 不存在")

        agent_result = await session.execute(
            select(AgentRecord).where(AgentRecord.id == conv.agent_id)
        )
        agent = agent_result.scalar_one_or_none()
        if agent is None:
            raise HTTPException(status_code=404, detail=f"Agent {conv.agent_id!r} 不存在")

    return StreamingResponse(
        run_agent_chat(agent, conv_id, body.message, file_ids=body.file_ids),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
