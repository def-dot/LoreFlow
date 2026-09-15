from app.registry import func


@func(
    label="查询notion笔记",
    description="搜索我的notion笔记",
    params={"prompt": "搜索关键字"},
    output_schema={"type": "string", "description": "查询结果"},
    tool=False,
)
async def search_notion(prompt: str) -> str:
    return "查询到N条数据"