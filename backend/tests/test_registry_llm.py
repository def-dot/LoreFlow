"""注册表 LLM 节点 — 不依赖 Ollama 服务的确定性路径"""

from app.registry.llm import intent_is, llm_classify, llm_rag_reply


async def test_classify_human_keyword_short_circuits() -> None:
    """提示词包含「人工」→ 直接判 human，不过模型（确定性、优先级最高）。"""
    out = await llm_classify({"prompt": "别让机器人回答，我要找人工客服"})
    assert out == {"intent": "human", "raw": "关键词命中：人工"}


def test_intent_is_finds_upstream_by_shape() -> None:
    """意图识别节点中英文命名均可 —— 按输出形状找上游，不依赖 ctx 键名。"""
    ctx = {"prompt": "你好", "意图识别": {"intent": "chat", "raw": "chat"}}
    assert intent_is(ctx, value="chat") is True
    assert intent_is(ctx, value="rag") is False
    assert intent_is({"prompt": "你好"}, value="chat") is False  # 无上游输出 → False


async def test_rag_reply_without_chunks_message() -> None:
    """无检索片段（支路未走/被跳过）→ 明确告知，不调用模型。"""
    assert await llm_rag_reply({"prompt": "北境要塞是什么"}) == "知识库中没有检索到相关内容。"
