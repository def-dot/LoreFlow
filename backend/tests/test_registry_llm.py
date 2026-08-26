"""注册表 LLM 节点 — 不依赖 Ollama 服务的确定性路径"""

from app.registry.llm import llm_classify


async def test_classify_human_keyword_short_circuits() -> None:
    """提示词包含「人工」→ 直接判 human，不过模型（确定性、优先级最高）。"""
    out = await llm_classify({"prompt": "别让机器人回答，我要找人工客服"})
    assert out == {"intent": "human", "raw": "关键词命中：人工"}
