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
import time
from collections.abc import AsyncGenerator
from typing import Any

from sqlmodel import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.agent import AgentRecord, MessageRecord
from app.registry.tool import execute_tool_call
from app.services.agent_tools import build_tools, build_skill_prompt
from app.services.llm import llm_chat_stream

logger = logging.getLogger(__name__)


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

    # 1. 构建 user message 并持久化
    if file_ids:
        file_list = "\n".join(f"- /uploads/{fid}" for fid in file_ids)
        user_message = f"[附件]\n{file_list}\n\n{user_message}"

    async with AsyncSessionLocal() as session:
        session.add(MessageRecord(conversation_id=conversation_id, role="user", content=user_message))
        await session.commit()

    # 2. 加载历史（含刚写入的 user message）
    messages = await _load_history(conversation_id)

    # 3. 插入 system prompt
    if agent.system_prompt or agent.skills:
        skill_prompt = build_skill_prompt(agent.skills) or ""
        system_prompt = (agent.system_prompt or "") + "\n\n" + skill_prompt
        messages.insert(0, {"role": "system", "content": system_prompt.strip()})

    # 4. 构建工具列表
    tool_names: list[str] = list(agent.tools or [])
    if agent.skills and "*" not in tool_names:
        for t in ("read_file", "run_code"):
            if t not in tool_names:
                tool_names.append(t)
    tools = build_tools(tool_names)

    # 5. Agentic loop
    max_iter = settings.AGENT_MAX_ROUNDS
    full_content = ""
    _chat_start = time.monotonic()

    try:
        final_content = ""

        for iteration in range(1, max_iter + 1):
            logger.info("[agent_chat] conv=%d iteration=%d/%d", conversation_id, iteration, max_iter)
            full_content = ""
            reasoning_content = ""
            current_tool_calls: list[dict[str, Any]] = []

            async for chunk in llm_chat_stream(agent.model or None, messages, tools=tools):
                reasoning_delta = chunk.get("reasoning", "")
                if reasoning_delta:
                    reasoning_content += reasoning_delta
                    yield f'event: thinking\ndata: {json.dumps({"content": reasoning_delta}, ensure_ascii=False)}\n\n'

                content_delta = chunk.get("content", "")
                if content_delta:
                    full_content += content_delta
                    yield f'event: token\ndata: {json.dumps({"content": content_delta}, ensure_ascii=False)}\n\n'

                if chunk.get("tool_calls"):
                    current_tool_calls = chunk["tool_calls"]

            final_content = full_content

            if not current_tool_calls:
                # 最终回复，入库
                async with AsyncSessionLocal() as session:
                    session.add(MessageRecord(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_content,
                        reasoning_content=reasoning_content or None,
                    ))
                    await session.commit()
                break

            tool_results: list[dict[str, Any]] = []
            for tc in current_tool_calls:
                func = tc.get("function", {})
                yield f'event: tool_start\ndata: {json.dumps({"tool_name": func.get("name", ""), "arguments": func.get("arguments", "")}, ensure_ascii=False)}\n\n'

                _t0 = time.monotonic()
                tr = await execute_tool_call(tc)
                duration_ms = int((time.monotonic() - _t0) * 1000)
                tr["arguments"] = func.get("arguments", "")
                tr["duration_ms"] = duration_ms
                tool_results.append(tr)

                yield f'event: tool_end\ndata: {json.dumps({"tool_name": tr["tool_name"], "output": tr["output"], "duration_ms": duration_ms, "status": tr.get("status", "success")}, ensure_ascii=False)}\n\n'

            # 本轮 assistant + tool 消息入库
            async with AsyncSessionLocal() as session:
                session.add(MessageRecord(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=full_content,
                    reasoning_content=reasoning_content or None,
                    tool_calls=current_tool_calls,
                ))
                for tr in tool_results:
                    session.add(MessageRecord(
                        conversation_id=conversation_id,
                        role="tool",
                        content=tr["output"],
                        tool_call_id=tr["tool_call_id"],
                    ))
                await session.commit()

            messages.append({
                "role": "assistant",
                "content": full_content,
                "tool_calls": current_tool_calls,
            })
            for tr in tool_results:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_call_id"],
                    "content": tr["output"],
                })
        else:
            logger.warning("[agent_chat] max iterations (%d) reached for conv=%d", max_iter, conversation_id)

        total_ms = int((time.monotonic() - _chat_start) * 1000)
        yield f'event: done\ndata: {json.dumps({"content": full_content, "total_duration_ms": total_ms}, ensure_ascii=False)}\n\n'

    except Exception as exc:
        logger.exception("[agent_chat] error in conv=%d", conversation_id)
        yield f'event: error\ndata: {json.dumps({"message": str(exc)}, ensure_ascii=False)}\n\n'
