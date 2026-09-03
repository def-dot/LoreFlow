"""注册表 LLM 节点 — 不依赖 Ollama 服务的确定性路径"""

import pytest

from app.registry.llm import final_answer, llm_classify


async def test_classify_human_keyword_short_circuits() -> None:
    """提示词包含「人工」→ 直接判 human，不过模型（确定性、优先级最高）。"""
    out = await llm_classify({"prompt": "别让机器人回答，我要找人工客服"})
    assert out == {"intent": "human", "raw": "关键词命中：人工"}


async def test_final_answer_picks_executed_branch() -> None:
    """互斥分支汇合：_upstream 按 depends_on 顺序取第一个非空输出（未执行支路为 None）。"""
    assert await final_answer({"_upstream": {"llm_chat": "你好呀", "rag_answer": None}}) == {
        "branch": "llm_chat", "answer": "你好呀",
    }
    assert await final_answer({"_upstream": {"llm_chat": None, "rag_answer": "北境要塞……"}}) == {
        "branch": "rag_answer", "answer": "北境要塞……",
    }
    with pytest.raises(ValueError):
        await final_answer({"_upstream": {"llm_chat": None, "rag_answer": None}})
    with pytest.raises(ValueError):
        await final_answer({})  # 未声明 depends_on → 无 _upstream
