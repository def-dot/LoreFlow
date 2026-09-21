from pydantic import BaseModel, ConfigDict, Field


class Node(BaseModel):
    # 进制额外字段，防止拼写错误
    model_config = ConfigDict(extra="forbid")

    name: str
    node_type: str = Field(alias="type")


class Pipeline(BaseModel):
    name: str
    nodes: list[Node]


# 准备一份有多个错误的数据
bad_data = {
    "name": "My Pipeline",
    "nodes": [
        {"name": "Node A", "type": "llm", "condtion": "x"},  # 错误 1: 字段名拼错 condtion
        {"type": "llm"},  # 错误 2: 缺失 name 字段
        {"name": 12345, "type": "tool"},  # 错误 3: name 类型不对（且无法强转时）
    ],
}

try:
    Pipeline.model_validate(bad_data)
except Exception as e:
    # 打印所有的错误项
    print(f"共捕获到 {len(e.errors())} 个错误：\n")
    for err in e.errors():
        print(f"错题路径 (loc) : {err['loc']}")
        print(f"错误类型 (type): {err['type']}")
        print(f"错误信息 (msg) : {err['msg']}")
        print("-" * 40)