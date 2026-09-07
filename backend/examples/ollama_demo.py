import json
import requests


# 本地函数
def get_current_weather(location):
    if "北京" in location:
        return json.dumps({"temperature": "20°C", "condition": "晴朗"})
    return json.dumps({"temperature": "未知", "condition": "未知"})


url = "http://localhost:11434/api/chat"

# 1. 定义 tools
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "获取城市当前天气",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "城市名称",
                    }
                },
                "required": ["location"],
            },
        },
    }
]

messages = [{"role": "user", "content": "北京今天天气怎么样？"}]

# 2. 发送第一次请求
payload = {
    "model": "qwen2.5:latest",
    "messages": messages,
    "tools": tools,
    "stream": False,  # 关闭流式返回，方便提取 JSON
}

response = requests.post(url, json=payload).json()
assistant_message = response["message"]

# 3. 检查是否触发工具调用
if "tool_calls" in assistant_message and assistant_message["tool_calls"]:
    print("-> 模型触发工具调用...")
    messages.append(assistant_message)  # 将模型的响应追加到历史

    # 遍历工具调用列表
    for tool_call in assistant_message["tool_calls"]:
        func_name = tool_call["function"]["name"]
        func_args = tool_call["function"]["arguments"]

        if func_name == "get_current_weather":
            # 执行本地逻辑
            result = get_current_weather(func_args.get("location"))

            # 追加工具回复
            messages.append({"role": "tool", "content": result})

    # 4. 发送第二次请求获取最终回答
    second_payload = {
        "model": "qwen2.5:latest",
        "messages": messages,
        "stream": False,
    }

    final_response = requests.post(url, json=second_payload).json()
    print("\n最终回答：", final_response["message"]["content"])
else:
    print("直接回答：", assistant_message["content"])
