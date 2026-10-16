"""独立 Agent 对话服务 — 多轮流式对话核心逻辑。

SSE 流式输出（token 级）、工具调用生命周期事件、消息持久化。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from sqlmodel import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.agent import AgentRecord, ChatFileRecord, ConversationRecord, MessageRecord
from app.schemas.agents import FileRef
from app.services.agent_tools import build_skill_prompt, build_tools, execute_tool_call
from app.services.llm import llm_chat_stream

logger = logging.getLogger(__name__)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _load_history(conversation_id: int) -> list[dict[str, Any]]:
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


async def run_agent_chat(
    conversation_id: int,
    user_message: str,
    *,
    files: list[FileRef] | None = None,
) -> AsyncGenerator[str, None]:
    """SSE 流式 agent 对话。yield SSE event 字符串。

    事件格式 (每行一个 SSE frame)：
    - ``event: token\\ndata: {"content": "..."}\\n\\n``
    - ``event: thinking\\ndata: {"content": "..."}\\n\\n``
    - ``event: tool_start\\ndata: {"tool_name": "...", "arguments": "..."}\\n\\n``
    - ``event: tool_end\\ndata: {"tool_name": "...", "output": "..."}\\n\\n``
    - ``event: done\\ndata: {"content": "..."}\\n\\n``
    - ``event: error\\ndata: {"message": "..."}\\n\\n``
    """
    messages: list[dict[str, Any]] = []
    full_content = ""
    _chat_start = time.monotonic()
    try:
        # 1. 加载 Agent 配置
        async with AsyncSessionLocal() as session:
            agent = (await session.exec(
                select(AgentRecord)
                .join(ConversationRecord, ConversationRecord.agent_id == AgentRecord.id)
                .where(ConversationRecord.id == conversation_id)
            )).one()

        # 2. 构建 system prompt
        system_parts = [agent.system_prompt or "", build_skill_prompt(agent.skills) or ""]
        system_content = "\n\n".join(p for p in system_parts if p)
        if system_content:
            messages.append({"role": "system", "content": system_content})

        # 3. 加载历史
        messages.extend(await _load_history(conversation_id))

        # 4. 处理附件 + 持久化 user message
        if files:
            file_list = "\n".join(f"- {f.store_path}" for f in files)
            user_message = f"[附件]\n{file_list}\n\n{user_message}"

        async with AsyncSessionLocal() as session:
            user_msg = MessageRecord(conversation_id=conversation_id, role="user", content=user_message)
            session.add(user_msg)
            await session.flush()  # 获取 message id
            for f in files or []:
                session.add(ChatFileRecord(
                    conversation_id=conversation_id,
                    message_id=user_msg.id,
                    store_path=f.store_path,
                    filename=f.filename,
                ))
            await session.commit()

        messages.append({"role": "user", "content": user_message})

        # 5. 构建工具列表
        tool_names: list[str] = list(agent.tools or [])
        if agent.skills and "load_skill" not in tool_names:
            tool_names.append("load_skill")
        tool_defs = build_tools(tool_names)

        # 6. Agentic loop
        for iteration in range(1, settings.AGENT_MAX_ROUNDS + 1):
            logger.info("[agent_chat] conv=%d iteration=%d/%d", conversation_id, iteration, settings.AGENT_MAX_ROUNDS)
            full_content = ""
            reasoning_content = ""
            current_tool_calls: list[dict[str, Any]] = []

            async for chunk in llm_chat_stream(model_key=agent.model, messages=messages, tools=tool_defs):
                if chunk.get("reasoning"):
                    reasoning_content += chunk["reasoning"]
                    yield _sse("thinking", {"content": chunk["reasoning"]})

                if chunk.get("content"):
                    full_content += chunk["content"]
                    yield _sse("token", {"content": chunk["content"]})

                if chunk.get("tool_calls"):
                    current_tool_calls = chunk["tool_calls"]

            async with AsyncSessionLocal() as session:
                session.add(MessageRecord(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_content,
                    reasoning_content=reasoning_content or None,
                    tool_calls=current_tool_calls,
                ))
                await session.commit()

            if not current_tool_calls:
                # 最终回复，入库
                break

            # 执行工具调用
            tool_messages: list[dict[str, Any]] = []
            for tc in current_tool_calls:
                func = tc.get("function", {})
                yield _sse("tool_start", {"tool_name": func.get("name", ""), "arguments": func.get("arguments", "")})

                _t0 = time.monotonic()
                output = await execute_tool_call(tc)
                duration_ms = int((time.monotonic() - _t0) * 1000)

                yield _sse("tool_end", {"tool_name": func.get("name", ""), "output": output, "duration_ms": duration_ms})

                tool_messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": output})

                async with AsyncSessionLocal() as session:
                    session.add(MessageRecord(
                        conversation_id=conversation_id,
                        role="tool",
                        content=output,
                        tool_call_id=tc.get("id", ""),
                        duration_ms=duration_ms,
                    ))
                    await session.commit()

            # 追加到上下文供下轮使用
            messages.append({"role": "assistant", "content": full_content, "tool_calls": current_tool_calls})
            messages.extend(tool_messages)

        else:
            logger.warning("[agent_chat] max iterations (%d) reached for conv=%d", settings.AGENT_MAX_ROUNDS, conversation_id)

        yield _sse("done", {"content": full_content, "total_duration_ms": int((time.monotonic() - _chat_start) * 1000)})

    except Exception as exc:
        logger.exception("[agent_chat] error in conv=%d", conversation_id)
        yield _sse("error", {"message": str(exc)})
