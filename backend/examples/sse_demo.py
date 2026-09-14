"""SSE 示例 — sessionId 隔离 + endpoint 通知。

运行：
    终端 1  uvicorn backend.examples.sse_demo:app --port 9000
    终端 2  python -m backend.examples.sse_demo
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse

app = FastAPI()


# ── 服务端 ──────────────────────────────────────────────────────────────────

sessions: dict[str, asyncio.Queue[str]] = {}


@app.post("/send")
async def send(msg: dict, sessionId: str = Query(...)):
    """POST 发消息 → 写入对应 session 的队列。"""
    queue = sessions.get(sessionId)
    if queue is None:
        return {"error": "session not found"}
    await queue.put(msg.get("text", ""))
    return {"ok": True}


@app.get("/stream")
async def stream(sessionId: str = Query(...)):
    """GET 订阅 → 建立连接，服务端推送该 session 的事件。"""
    queue = sessions.setdefault(sessionId, asyncio.Queue())

    async def generate():
        yield f'event: endpoint\ndata: {json.dumps({"url": f"/send?sessionId={sessionId}"})}\n\n'

        while True:
            try:
                text = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ":keepalive\n\n"
                continue
            data = json.dumps({"text": text, "ts": time.time()}, ensure_ascii=False)
            yield f"event: message\ndata: {data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 客户端 ──────────────────────────────────────────────────────────────────

BASE = "http://localhost:9000"


async def listen(client: httpx.AsyncClient, sessionId: str, endpoint: asyncio.Future[str]):
    """消费 SSE 流，收到 endpoint 就通知 send_all，收到消息就打印。"""
    last_event = ""

    async with client.stream("GET", f"/stream?sessionId={sessionId}") as resp:
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
    sessionId = uuid.uuid4().hex[:8]
    print(f"sessionId: {sessionId}")

    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as client:
        endpoint: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        done, _ = await asyncio.wait(
            [asyncio.create_task(listen(client, sessionId, endpoint)),
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