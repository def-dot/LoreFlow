"""
LLM 节点 — 调用本地 Ollama 服务（默认 http://localhost:11434）。

输入约定：节点函数从共享上下文取值（YAML ``params`` 声明 + 创建运行时
提供），模型名可被 ``ctx["model"]`` 覆盖，缺省取 ``settings.OLLAMA_MODEL``。
连接/超时/模型不存在统一转成中文 ValueError，节点结果里直接可读。
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.registry.core import node
from app.utils.http import http_client


async def _ollama_chat(model: str, messages: list[dict[str, str]]) -> str:
    """POST /api/chat（非流式）→ 助手回复文本。"""
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    resp = await http_client().post(
        url, json={"model": model, "messages": messages, "stream": False}
    )
    resp.raise_for_status()
    data = resp.json()
        
    content = data["message"]["content"]
    return str(content)


@node(
    label="LLM 对话",
    description=(
        "读取 ctx['prompt'] 调用本地 Ollama 生成回答"
    ),
)
async def llm_chat(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML params 声明为必填，创建运行时提供）")
    content = prompt
    pages = ctx.get("pages")  # 上游 web_fetch 节点（YAML 里命名为 pages）的输出
    if isinstance(pages, list) and pages:
        refs = "\n\n".join(
            f"[网页 {i}] {page.get('url')}\n{page.get('text', '')}"
            for i, page in enumerate(pages, 1)
            if isinstance(page, dict)
        )
        content = f"{prompt}\n\n---\n以下是提示词中链接对应的网页内容，回答时请参考：\n\n{refs}"
    messages: list[dict[str, str]] = []
    system = ctx.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})
    model = str(ctx.get("model") or settings.OLLAMA_MODEL)
    return await _ollama_chat(model, messages)


@node(
    label="意图识别",
    description="LLM 判断 ctx['prompt'] 是简单问答（simple）还是复杂诉求（complex），输出 {intent, raw}",
)
async def llm_classify(ctx: dict[str, Any]) -> dict[str, Any]:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML params 声明为必填，创建运行时提供）")
    system = (
        "你是客服意图分类器，只输出一个小写单词，不要输出任何其他内容：\n"
        "simple —— 常见问题，凭知识库资料即可直接回答\n"
        "complex —— 投诉、纠纷、个性化或多步骤等需要人工客服处理的复杂诉求"
    )
    raw = await _ollama_chat(
        settings.OLLAMA_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
    )
    # 宽松解析：本地模型不总按指令输出小写英文（实测会把闲聊答成「聊天」），
    # 按中英关键词识别；识别不出按简单问答（自动回复只依据知识库作答，
    # 资料不足会明说，不至于把诉求错误升级成人工）
    text = raw.lower()
    intent = "complex" if any(k in text for k in ("complex", "复杂", "人工", "投诉", "纠纷")) else "simple"
    return {"intent": intent, "raw": raw.strip()}


@node(kind="condition", label="是简单问答", description="意图为 simple：知识库自动回复支路执行")
def is_simple(ctx: dict[str, Any]) -> bool:
    classify = ctx.get("classify")
    return isinstance(classify, dict) and classify.get("intent") == "simple"


@node(kind="condition", label="是复杂诉求", description="意图为 complex：人工客服接管支路执行")
def is_complex(ctx: dict[str, Any]) -> bool:
    classify = ctx.get("classify")
    return isinstance(classify, dict) and classify.get("intent") == "complex"


@node(
    label="知识库问答",
    description="结合检索片段回答 ctx['prompt']（片段来自 YAML 命名为 retrieve 的 rag_retrieve 节点输出）",
)
async def llm_rag_reply(ctx: dict[str, Any]) -> str:
    prompt = ctx.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少提示词：prompt 必须是非空字符串（在 YAML params 声明为必填，创建运行时提供）")
    chunks = ctx.get("retrieve") or []
    if not isinstance(chunks, list) or not chunks:
        return "知识库中没有检索到相关内容。"
    refs = "\n\n".join(
        f"[{i}] {c.get('source', '')} {c.get('text', '')}"
        for i, c in enumerate(chunks, 1)
        if isinstance(c, dict)
    )
    system = "你是知识库问答助手。仅依据参考资料回答问题；资料不足以回答时明确说明，不要编造。"
    user = f"参考资料：\n{refs}\n\n问题：{prompt}"
    return await _ollama_chat(
        settings.OLLAMA_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
