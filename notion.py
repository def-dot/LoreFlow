from app.registry import node_type

@node_type(
    label="查询notion笔记",
    description="搜索我的notion笔记",
    input_schema={
        "prompt": {"type": "string", "required": True, "description": "搜索关键字"},
    },
    output_schema={"type": "string", "description": "查询结果"},
)
async def search_notion(ctx: dict) -> str:
    return "查询到N条数据"