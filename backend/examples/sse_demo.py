"""SSE 示例 — sessionId + endpoint + heartbeat + Last-Event-ID 断线重连。

运行：
    终端 1  uvicorn backend.examples.sse_demo:app --port 9000
    终端 2  python -m backend.examples.sse_demo
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Header, Query
from fastapi.responses import StreamingResponse

app = FastAPI()


# ── 服务端 ──────────────────────────────────────────────────────────────────

@dataclass
class Session:
    queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    history: list[tuple[int, str]] = field(default_factory=list)  # (id, text)
    counter: int = 0


sessions: dict[str, Session] = {}


@app.post("/send")
async def send(msg: dict, sessionId: str = Query(...)):
    """POST 发消息 → 写入队列 + 记录历史（供重连回放）。"""
    session = sessions.get(sessionId)
    if session is None:
        return {"error": "session not found"}
    session.counter += 1
    text = msg.get("text", "")
    session.history.append((session.counter, text))
    await session.queue.put(text)
    return {"ok": True, "id": session.counter}


@app.get("/stream")
async def stream(
    sessionId: str = Query(...),
    Last_Event_ID: str | None = Header(None, alias="Last-Event-ID"),
):
    """GET 订阅 → SSE 长连接。

    - 带 Last-Event-ID → 先回放丢失的消息，再接新消息
    - 不带             → 只接新消息
    """
    session = sessions.setdefault(sessionId, Session())

    async def generate():
        # endpoint 事件
        yield f'event: endpoint\ndata: {json.dumps({"url": f"/send?sessionId={sessionId}"})}\n\n'

        # 断线重连：回放 Last-Event-ID 之后的历史消息
        if Last_Event_ID is not None:
            after_id = int(Last_Event_ID)
            for mid, text in session.history:
                if mid > after_id:
                    data = json.dumps({"text": text, "ts": time.time()}, ensure_ascii=False)
                    yield f"id: {mid}\nevent: message\ndata: {data}\n\n"

        # 持续推送新消息
        while True:
            try:
                text = await asyncio.wait_for(session.queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ":keepalive\n\n"
                continue
            session.counter += 1
            session.history.append((session.counter, text))
            data = json.dumps({"text": text, "ts": time.time()}, ensure_ascii=False)
            yield f"id: {session.counter}\nevent: message\ndata: {data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── 客户端 ──────────────────────────────────────────────────────────────────

BASE = "http://localhost:9000"


async def listen(
    client: httpx.AsyncClient,
    sessionId: str,
    endpoint: asyncio.Future[str],
    last_id: list[int],       # 用 list 包装，方便在闭包里修改
    reconnect_after: int = 0,  # 收到第几条后断开（0=不断）
):
    """消费 SSE 流，解析所有字段，跟踪 last_id。"""
    event_type = ""
    received = 0
    headers = {}

    if last_id[0] > 0:
        headers["Last-Event-ID"] = str(last_id[0])
        print(f"  [重连] Last-Event-ID={last_id[0]}")

    async with client.stream("GET", f"/stream?sessionId={sessionId}", headers=headers) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("id:"):
                last_id[0] = int(line.removeprefix("id:").strip())
            elif line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = json.loads(line.removeprefix("data:").strip())
                if event_type == "endpoint":
                    endpoint.set_result(data["url"])
                    print(f"← endpoint: {data['url']}")
                elif event_type == "message":
                    received += 1
                    print(f"← 收到: {data['text']}  (id={last_id[0]})")
                    if reconnect_after and received >= reconnect_after:
                        print("  [主动断开，模拟网络中断]")
                        return  # 退出 → 触发重连


async def send_all(client: httpx.AsyncClient, endpoint: asyncio.Future[str]):
    """等 endpoint 就绪后，往该地址发消息。"""
    url = await endpoint
    for text in ["你好", "今天天气怎么样", "再见", "第四条", "第五条"]:
        print(f"→ 发送: {text}")
        await client.post(url, json={"text": text})
        await asyncio.sleep(1)


async def run_client():
    sessionId = uuid.uuid4().hex[:8]
    last_id = [0]
    print(f"sessionId: {sessionId}")

    async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as client:
        endpoint: asyncio.Future[str] = asyncio.get_event_loop().create_future()

        # 第一次连接：收到 2 条后断开
        await listen(client, sessionId, endpoint, last_id, reconnect_after=2)

        # 模拟断线间隔
        await asyncio.sleep(1)

        # 重连：带 Last-Event-ID，补收丢失的消息
        endpoint2: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        listen_task = asyncio.create_task(
            listen(client, sessionId, endpoint2, last_id)
        )
        send_task = asyncio.create_task(send_all(client, endpoint2))

        await asyncio.wait(
            [listen_task, send_task],
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