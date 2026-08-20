"""示例插件 — 通知类节点：演示插件与内置节点混用。

放在 PLUGINS_DIR（默认 custom_plugins/）下的 .py 文件会被自动加载：
唯一要求是用 @node 装饰器定义函数，导入即注册，无需任何额外接线。
使用示例见 app/pipelines/07_plugin_demo.yaml。
"""

from app.registry import node


@node(label="生成通知", description="插件节点：基于 merge 输出生成通知文本")
async def notify_message(ctx: dict) -> str:
    merge = ctx["merge"]
    return f"[通知] {merge['title']}（正文 {len(merge['body'])} 字符）已就绪"


@node(kind="condition", label="长文通知", description="插件条件：正文超过 20 字符才生成通知")
def notify_long_body(ctx: dict) -> bool:
    merge = ctx.get("merge")
    return bool(merge) and len(str(merge.get("body", ""))) > 20
