"""最简 SSE 示例 — 服务端推送 + 客户端消费。

运行：
    终端 1  uvicorn backend.examples.sse_demo:app --port 9000
    终端 2  python -m backend.examples.sse_demo
"""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


# ── 服务端 ──────────────────────────────────────────────────────────────────

messages: asyncio.Queue[str] = asyncio.Queue()


@app.post("/send")
async def send(msg: dict):
    """POST 发消息 → 写入队列。"""
    await messages.put(msg.get("text", ""))
    return {"ok": True}


@app.get("/stream")
async def stream():
    """GET 订阅 → SSE 长连接，从队列读取并推送。"""

    async def generate():
        # 连接建立后，服务端告知客户端往哪发消息
        yield f'event: endpoint\ndata: {json.dumps({"url": "/send"})}\n\n'

        while True:
            text = await messages.get()
            data = json.dumps({"text": text, "ts": time.time()}, ensure_ascii=False)
            yield f"event: message\ndata: {data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 客户端 ──────────────────────────────────────────────────────────────────

BASE = "http://localhost:9000"


async def listen(client: httpx.AsyncClient, endpoint: asyncio.Future[str]):
    """消费 SSE 流，收到 endpoint 就通知 send_all，收到消息就打印。"""
    last_event = ""

    async with client.stream("GET", "/stream") as resp:
        async for line in resp.aiter_lines():
            if line.startswith("event:"):
                last_event = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = json.loads(line.removeprefix("data:").strip())
                if last_event == "endpoint":
                    endpoint.set_result(data["url"])
                    print(f"← endpoint: {data['url']}")
                elif last_event == "message":
                    print(f"← 收到: {data['text']}")


async def send_all(client: httpx.AsyncClient, endpoint: asyncio.Future[str]):
    """等 endpoint 就绪后，往该地址发消息。"""
    url = await endpoint
    for text in ["你好", "今天天气怎么样", "再见"]:
        print(f"→ 发送: {text}")
        await client.post(url, json={"text": text})
        await asyncio.sleep(1)


async def run_client():
    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as client:
        endpoint: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        done, _ = await asyncio.wait(
            [asyncio.create_task(listen(client, endpoint)),
             asyncio.create_task(send_all(client, endpoint))],
            return_when=asyncio.FIRST_COMPLETED,
        )


if __name__ == "__main__":
    import argparse, uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--client", action="store_true")
    args = parser.parse_args()

    if args.client:
        asyncio.run(run_client())
    else:
        uvicorn.run(app, port=9000)
