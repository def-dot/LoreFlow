import asyncio
from app.engine import DAG
from app.engine.validate import validate_inputs

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

inputs = {"title": "Hello", "content": "World"}
errors = dag.validate() + validate_inputs(inputs, dag.params)   # 调用方组合：结构 + 输入
if errors:
    raise ValueError("\n".join(errors))                         # run 信任调用方，不再自校验

results = await dag.run(inputs=inputs)