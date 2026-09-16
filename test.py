from pydantic import BaseModel, Field
from typing import Optional
import json

class WebSearchItem(BaseModel):
    title: str = Field(description="结果标题")
    url: str = Field(description="结果链接")
    content: str = Field(description="结果摘要")
    
class WebSearchOutput(BaseModel):
    result: list[WebSearchItem] = Field(description="搜索结果列表")

# ====================
# 直接对【类】进行序列化
# ====================

# 1. 获取该类的 JSON Schema（返回的是 Python 字典）
schema_dict = WebSearchOutput.model_json_schema()

# print("--- JSON Schema 字典 ---")
# print(schema_dict)

# 2. 如果需要将其序列化为标准 JSON 字符串
schema_json_str = json.dumps(schema_dict, ensure_ascii=False, indent=2)

print("\n--- JSON Schema 字符串 ---")
print(schema_json_str)