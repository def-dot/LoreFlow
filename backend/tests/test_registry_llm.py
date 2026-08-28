"""注册表 LLM 节点 — 不依赖 Ollama 服务的确定性路径"""

import pytest

from app.registry.llm import final_answer, llm_classify, llm_rag_reply


async def test_classify_human_keyword_short_circuits() -> None:
    """提示词包含「人工」→ 直接判 human，不过模型（确定性、优先级最高）。"""
    out = await llm_classify({"prompt": "别让机器人回答，我要找人工客服"})
    assert out == {"intent": "human", "raw": "关键词命中：人工"}


async def test_rag_reply_without_chunks_message() -> None:
    """无检索片段（支路未走/被跳过）→ 明确告知，不调用模型。"""
    assert await llm_rag_reply({"prompt": "北境要塞是什么"}) == "知识库中没有检索到相关内容。"


async def test_final_answer_picks_executed_branch() -> None:
    """互斥分支汇合：_upstream 按 depends_on 顺序取第一个非空输出（未执行支路为 None）。"""
    assert await final_answer({"_upstream": {"llm_chat": "你好呀", "llm_rag_reply": None}}) == {
        "branch": "llm_chat", "answer": "你好呀",
    }
    assert await final_answer({"_upstream": {"llm_chat": None, "llm_rag_reply": "北境要塞……"}}) == {
        "branch": "llm_rag_reply", "answer": "北境要塞……",
    }
    with pytest.raises(ValueError):
        await final_answer({"_upstream": {"llm_chat": None, "llm_rag_reply": None}})
    with pytest.raises(ValueError):
        await final_answer({})  # 未声明 depends_on → 无 _upstream
