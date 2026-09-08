"""
内置工具
"""

from app.registry.tools import tool
from app.utils.http import http_client


@tool(description="查询指定城市的当前天气信息",
      params={"city": "城市名称，如北京、上海"})
async def get_weather(city: str) -> str:
    """通过 wttr.in 查询实时天气。"""

    try:
        resp = await http_client().get(f"https://wttr.in/{city}", params={"format": "j1"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        cur = data["current_condition"][0]
        desc = cur.get("lang_zh", [{}])[0].get("value") or cur.get("weatherDesc", [{}])[0].get("value", "")
        temp = cur.get("temp_C", "?")
        humidity = cur.get("humidity", "?")
        wind = cur.get("windspeedKmph", "?")
        wind_dir = cur.get("winddir16Point", "")
        return f"{city}：{desc}，气温 {temp}°C，湿度 {humidity}%，风速 {wind} km/h {wind_dir}"
    except Exception as exc:
        return f"{city}：天气查询失败（{type(exc).__name__}: {exc}）"


@tool(name="calculator", description="执行数学计算表达式并返回结果",
      params={"expression": "数学表达式，如 123 * 456、sqrt(144)"})
async def calculator(expression: str = "") -> str:
    """计算器 — 替换为安全的表达式求值库。"""
    try:
        return str(eval(expression))
    except Exception:
        return f"计算错误：无法计算表达式 {expression}"
