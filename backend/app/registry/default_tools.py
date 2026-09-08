"""
内置工具
"""

import asyncio

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


@tool(description="执行 Python 代码并返回输出",
      params={"code": "要执行的 Python 代码"})
async def run_code(code: str) -> str:
    """在子进程中执行 Python 代码，返回 stdout 和 stderr。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "python", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        parts: list[str] = []
        if stdout:
            parts.append(stdout.decode(errors="replace"))
        if stderr:
            parts.append(f"[stderr]\n{stderr.decode(errors='replace')}")
        if proc.returncode != 0:
            parts.append(f"[exit code] {proc.returncode}")
        return "\n".join(parts) or "(无输出)"
    except asyncio.TimeoutError:
        return "执行超时（60 秒）"
    except Exception as exc:
        return f"执行失败：{type(exc).__name__}: {exc}"
