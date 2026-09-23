from pydantic import BaseModel, Field

from app.registry import func


class SearchNotionInput(BaseModel):
    prompt: str = Field(description="搜索关键字")


class SearchNotionOutput(BaseModel):
    result: str = Field(description="查询结果")


@func(
    label="查询notion笔记",
    description="搜索我的notion笔记",
    tool=False,
)
async def search_notion(params: SearchNotionInput) -> SearchNotionOutput:
    return SearchNotionOutput(result="查询到N条数据")