"""SSE vs Streamable HTTP 适用场景对比。

场景一  SSE（服务端主动推）— 股票行情，客户端只管接，不需要发请求
场景二  Streamable HTTP（客户端请求，服务端流式回复）— 类 ChatGPT 对话

运行：
    终端 1  uvicorn backend.examples.streamable_http_demo:app --port 8000
    终端 2  python -m backend.examples.streamable_http_demo --client sse
    终端 3  python -m backend.examples.streamable_http_demo --client streamable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


# ── 场景一：SSE — 服务端主动推（股票行情）─────────────────────────────────────

@app.get("/stock")
async def stock():
    """服务端每秒主动推送股价，客户端不需要发任何请求。"""

    async def generate():
        price = 100.0
        while True:
            price += random.uniform(-1, 1)
            data = json.dumps({"symbol": "AAPL", "price": round(price, 2), "ts": time.time()})
            yield f"data: {data}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 场景二：Streamable HTTP — 客户端请求，服务端流式回复（对话）─────────────

@app.post("/chat")
async def chat(body: dict):
    """客户端发消息，服务端逐字流式回复——跟 ChatGPT 一样。"""

    async def generate():
        reply = f"你说的是「{body.get('text', '')}」，我收到了。"
        for ch in reply:
            await asyncio.sleep(0.05)
            yield f"data: {json.dumps({'ch': ch}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 客户端 ──────────────────────────────────────────────────────────────────

async def _run_sse():
    """场景一：挂着不动，服务端一直推。"""
    print("订阅股票行情（Ctrl+C 退出）\n")
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=None) as c:  # noqa: SIM117
        async with c.stream("GET", "/stock") as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = json.loads(line.removeprefix("data:").strip())
                print(f"  {data['symbol']}  ${data['price']}")


async def _run_streamable():
    """场景二：发请求，收流式回复。"""
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=10.0) as c:
        for text in ["你好", "今天天气怎么样", "再见"]:
            print(f"→ {text}")
            async with c.stream("POST", "/chat", json={"text": text}) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line.removeprefix("data:").strip())
                    if data.get("done"):
                        print()
                    else:
                        print(data["ch"], end="", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", choices=["sse", "streamable"], help="客户端模式")
    args = parser.parse_args()

    if args.client == "sse":
        asyncio.run(_run_sse())
    elif args.client == "streamable":
        asyncio.run(_run_streamable())
    else:
        import uvicorn
        uvicorn.run(app, port=8000)