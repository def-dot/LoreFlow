from pydantic import BaseModel, ConfigDict
from typing import Any

class RetryPolicy(BaseModel):
    max_retries: int = 3
    delay: float = 1.0

class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    name: str
    type: str
    retry: RetryPolicy | None = None

json_data = {
    "name": "node_1",
    "type": "task",
    "retry": {"max_retries": 5, "delay": 2.0}
}

# 直接实例化（解包字典）
node = Node(**json_data)

# 验证结果
print(type(node.retry))       # 输出: <class '__main__.RetryPolicy'>
print(node.retry.max_retries) # 输出: 5 （支持点语法访问）