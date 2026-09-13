"""MiMo Tool Calling 测试 — 通过 OpenAI 兼容 API 测试工具调用。"""

import json
import requests

BASE_URL = "https://token-plan-cn.xiaomimimo.com/v1"
API_KEY = "tp-ca8urd0ekskqst4n95vd1l2p06ldbth4f8okziv2ha9ltmgv"
MODEL = "mimo-v2.5-pro"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def chat(messages, tools=None):
    payload = {"model": MODEL, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    resp = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


# 本地工具
def get_weather(location):
    data = {"北京": "晴 25°C", "上海": "多云 22°C"}
    return json.dumps({"location": location, "weather": data.get(location, "未知")})


tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取城市天气",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string", "description": "城市名称"}},
                "required": ["location"],
            },
        },
    }
]

messages = [{"role": "user", "content": "北京和上海今天天气怎么样？"}]

# 第一轮：模型决定是否调用工具
print("发送请求...")
assistant = chat(messages, tools)

if "tool_calls" in assistant and assistant["tool_calls"]:
    print(f"-> 触发 {len(assistant['tool_calls'])} 个工具调用")
    messages.append(assistant)

    for tc in assistant["tool_calls"]:
        name = tc["function"]["name"]
        args = tc["function"]["arguments"]
        print(f"   {name}({args})")

        if name == "get_weather":
            result = get_weather(args.get("location"))
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    # 第二轮：获取最终回答
    final = chat(messages)
    print("\n最终回答：", final["content"])
else:
    print("\n直接回答：", assistant["content"])
