"""示例插件 — 文本统计与摘要：演示插件节点的两种常见模式。

放在 PLUGINS_DIR（默认 custom_plugins/）下的 .py 文件会被自动加载：
唯一要求是用 @node_type 装饰器定义函数，导入即注册，无需任何额外接线。

本插件提供两个节点：
  - text_stats    ：动作节点，读取上游输出并返回结构化统计
  - text_long_enough：条件节点，判断文本是否达到最小长度
"""

from __future__ import annotations

from app.registry import node_type


@node_type(label="文本统计", description="统计文本的字数、字符数，截取首句作为摘要")
async def text_stats(ctx: dict) -> dict:
    """动作节点示例：从 ctx 读取上游数据，返回结构化结果。

    在 YAML 中可通过 inputs 接线将上游节点的输出注入到 ctx['text']，
    例如  inputs: { text: $llm_chat } 。
    """
    text = str(ctx.get("text", ""))
    words = len(text.split())
    chars = len(text)
    # 取第一个句号/问号/感叹号之前的内容作为摘要
    first_sentence = text
    for sep in ("。", ".", "？", "?", "！", "!"):
        idx = text.find(sep)
        if idx > 0:
            first_sentence = text[: idx + 1]
            break
    return {
        "word_count": words,
        "char_count": chars,
        "summary": first_sentence[:200],  # 摘要最多 200 字符
    }


@node_type(label="文本足够长", description="条件节点：文本超过指定字符数时返回 True")
def text_long_enough(ctx: dict) -> bool:
    """条件节点示例：配合 YAML 中的 condition 字段实现分支。

    用法：condition: $text_stats.char_count > 0
    或者直接用本节点做门控：
        depends_on: [text_stats]
        condition: $text_long_enough
    """
    text = str(ctx.get("text", ""))
    min_chars = int(ctx.get("min_chars", 10))
    return len(text) >= min_chars
