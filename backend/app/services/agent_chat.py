"""独立 Agent 对话服务 — 多轮流式对话核心逻辑。

从 node_types/agent.py 的 agentic loop 提取并扩展，支持：
- 从 messages 表加载历史上下文
- SSE 流式输出（token 级）
- 工具调用的完整生命周期事件
- 消息持久化
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.agent import AgentRecord, ConversationRecord, MessageRecord
from app.registry.tool import execute_tool_calls
from app.services.agent_tools import build_skill_prompt, build_tools
from app.services.llm import llm_chat_stream

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 消息存取
# ---------------------------------------------------------------------------


async def _load_history(conversation_id: int) -> list[dict[str, str]]:
    """从 messages 表加载历史消息为 OpenAI messages 格式。"""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MessageRecord)
            .where(MessageRecord.conversation_id == conversation_id)
            .order_by(MessageRecord.id)
        )
        rows = result.scalars().all()

    messages: list[dict[str, Any]] = []
    for row in rows:
        if row.role == "user":
            messages.append({"role": "user", "content": row.content})
        elif row.role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": row.content}
            if row.tool_calls:
                msg["tool_calls"] = row.tool_calls
            messages.append(msg)
        elif row.role == "tool":
            messages.append({
                "role": "tool",
                "tool_call_id": row.tool_call_id or "",
                "content": row.content,
            })
    return messages


def _add_message(
    session: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    tool_calls: list[Any] | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
) -> None:
    """往 session 里加一条消息（不 commit，由调用方统一提交）。"""
    session.add(MessageRecord(
        conversation_id=conversation_id,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
    ))


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 对话执行
# ---------------------------------------------------------------------------


async def run_agent_chat(
    agent: AgentRecord,
    conversation_id: int,
    user_message: str,
    file_ids: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """SSE 流式 agent 对话。yield SSE event 字符串。

    事件格式 (每行一个 SSE frame)：
    - ``event: token\\ndata: {"content": "..."}\\n\\n``
    - ``event: tool_start\\ndata: {"tool_name": "...", "arguments": "..."}\\n\\n``
    - ``event: tool_end\\ndata: {"tool_name": "...", "output": "..."}\\n\\n``
    - ``event: done\\ndata: {"content": "..."}\\n\\n``
    - ``event: error\\ndata: {"message": "..."}\\n\\n``
    """

    # 1. 保存用户消息 + 更新对话时间 + 首条自动标题（一次 session）
    display_message = user_message
    if file_ids:
        file_list = "\n".join(f"- /uploads/{fid}" for fid in file_ids)
        display_message = f"[附件]\n{file_list}\n\n{user_message}"

    async with AsyncSessionLocal() as session:
        _add_message(session, conversation_id, "user", display_message)

        # 更新对话时间
        conv = await session.get(ConversationRecord, conversation_id)
        if conv:
            conv.updated_at = datetime.now()
            # 首条消息自动设标题
            if not conv.title:
                conv.title = user_message[:50]

        await session.commit()

    # 2. 加载历史上下文
    messages = await _load_history(conversation_id)

    # 3. 构建 system prompt
    system = agent.system_prompt or ""
    skill_prompt = build_skill_prompt(agent.skills or [])
    if skill_prompt:
        system = f"{skill_prompt}\n\n{system}" if system else skill_prompt

    if system:
        if not messages or messages[0].get("role") != "system":
            messages.insert(0, {"role": "system", "content": system})

    # 4. 构建工具列表
    tool_names: list[str] = list(agent.tools or [])
    if agent.skills and "*" not in tool_names:
        for t in ("read_file", "run_code"):
            if t not in tool_names:
                tool_names.append(t)
    tools = build_tools(tool_names)

    # 5. Agentic loop
    max_iter = agent.max_iterations or 5
    final_content = ""

    try:
        for iteration in range(1, max_iter + 1):
            logger.info("[agent_chat] conv=%d iteration=%d/%d", conversation_id, iteration, max_iter)

            full_content = ""
            current_tool_calls: list[dict[str, Any]] = []

            async for chunk in llm_chat_stream(agent.model or None, messages, tools=tools):
                content_delta = chunk.get("content", "")
                if content_delta:
                    full_content += content_delta
                    yield _sse("token", {"content": content_delta})

                if chunk.get("finish_reason") == "tool_calls" and chunk.get("tool_calls"):
                    current_tool_calls = chunk["tool_calls"]

            # 无工具调用 → 对话结束
            if not current_tool_calls:
                final_content = full_content
                async with AsyncSessionLocal() as session:
                    _add_message(session, conversation_id, "assistant", full_content)
                    conv = await session.get(ConversationRecord, conversation_id)
                    if conv:
                        conv.updated_at = datetime.now()
                    await session.commit()
                break

            # 有工具调用
            for tc in current_tool_calls:
                func = tc.get("function", {})
                yield _sse("tool_start", {
                    "tool_name": func.get("name", ""),
                    "arguments": func.get("arguments", ""),
                })

            # 执行工具
            tool_results = await execute_tool_calls(current_tool_calls)

            # 保存 assistant + 所有 tool 结果 + 更新对话时间（一次 session）
            async with AsyncSessionLocal() as session:
                _add_message(
                    session, conversation_id, "assistant", full_content,
                    tool_calls=current_tool_calls,
                )

                for tr in tool_results:
                    yield _sse("tool_end", {
                        "tool_name": tr["tool_name"],
                        "output": tr["output"][:500],
                    })

                    _add_message(
                        session, conversation_id, "tool",
                        f"[{tr['tool_name']}] {tr['output']}",
                        tool_call_id=tr["tool_call_id"],
                        tool_name=tr["tool_name"],
                    )

                conv = await session.get(ConversationRecord, conversation_id)
                if conv:
                    conv.updated_at = datetime.now()
                await session.commit()

            # 追加到上下文供下一轮 LLM 使用
            messages.append({
                "role": "assistant",
                "content": full_content,
                "tool_calls": current_tool_calls,
            })
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": f"[{tr['tool_name']}] {tr['output']}",
                })
        else:
            logger.warning("[agent_chat] max iterations (%d) reached for conv=%d", max_iter, conversation_id)
            if not final_content:
                final_content = full_content

        yield _sse("done", {"content": final_content})

    except Exception as exc:
        logger.exception("[agent_chat] error in conv=%d", conversation_id)
        yield _sse("error", {"message": str(exc)})
