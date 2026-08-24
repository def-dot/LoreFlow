import asyncio
from app.engine import DAG

dag = DAG(
    "人工审核",                                  # name:
    params={                                     # params: 同一形状原样传入
        "title": {"required": True, "label": "标题"},
        "content": {"required": True, "label": "正文", "multiline": True},
    },
)

async def auto_approver(node_name: str, payload: dict) -> dict:   # ← 关键差异，见下
    return {"approve": True}

dag.human_node(                                 # kind: human → human_node()
    "review",
    prompt="请审核文章内容是否可以发布",
    review={"title": {"label": "标题"}, "content": {"label": "正文"}},
    approver=auto_approver,
)

@dag.node("publish", depends_on=["review"])     # type: cfg_publish → 自己的函数
async def publish(ctx: dict) -> dict:
    return {"published": True, "title": ctx["title"]}

errors = dag.validate({"title": "Hello", "content": "World"})  

results = asyncio.run(dag.run(inputs={"title": "Hello", "content": "World"}))