"""注册表 LLM 节点 — 不依赖 Ollama 服务的确定性路径"""

import pytest
from pydantic import ValidationError

from app.registry.funcs.llm import LLMClassifyParams, llm_classify


async def test_classify_parses_structured_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    """结构化输出：响应 schema 拿 classify_labels 做 enum，解析后只暴露 intent。"""
    from app.registry.funcs import llm as llm_funcs

    async def fake_chat(model_key, messages, tools=None, response_format=None):
        schema = response_format["json_schema"]["schema"]
        assert schema["properties"]["intent"]["enum"] == ["chat", "human"]
        assert schema["required"] == ["intent"]
        assert schema["additionalProperties"] is False
        return {"content": '{"intent": "human"}', "tool_calls": []}

    monkeypatch.setattr(llm_funcs, "llm_chat_call", fake_chat)
    out = await llm_classify(LLMClassifyParams(prompt="我要找人工客服", classify_labels=["chat", "human"]))
    assert out.model_dump() == {"intent": "human"}


async def test_classify_rejects_label_outside_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    """动态模型按 classify_labels 校验，越界标签直接报错。"""
    from app.registry.funcs import llm as llm_funcs

    async def fake_chat(model_key, messages, tools=None, response_format=None):
        return {"content": '{"intent": "unknown"}', "tool_calls": []}

    monkeypatch.setattr(llm_funcs, "llm_chat_call", fake_chat)
    with pytest.raises(ValidationError):
        await llm_classify(LLMClassifyParams(prompt="你好", classify_labels=["chat", "human"]))
