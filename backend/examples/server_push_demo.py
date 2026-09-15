"""Streamable HTTP 服务端主动推送演示（模拟 MCP GET 流机制）。

核心思想：
  - POST /chat  → 客户端请求，服务端流式回复（request-response）
  - GET  /events → 服务端主动推送通知（server-initiated push）

这两个连接同时存在。POST 是一问一答，GET 是持续订阅。
MCP 的 Streamable HTTP 就是把两者合一：同一个 endpoint 同时处理两种。

运行：
    终端 1  uvicorn backend.examples.server_push_demo:app --port 8000
    终端 2  python -m backend.examples.server_push_demo --client
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


# ── 服务端 ──────────────────────────────────────────────────────────────────

@dataclass
class ClientSession:
    """模拟 MCP session：一个 POST 请求流 + 一个 GET 订阅流。"""
    notifications: asyncio.Queue[str] = field(default_factory=asyncio.Queue)


sessions: dict[str, ClientSession] = {}


async def _background_broadcaster():
    """后台任务：每 5 秒往所有 session 推一条通知（模拟服务端事件）。"""
    while True:
        await asyncio.sleep(5)
        notice = json.dumps({
            "type": "server_notification",
            "message": f"系统通知 @ {time.strftime('%H:%M:%S')}",
            "ts": time.time(),
        }, ensure_ascii=False)
        for session in sessions.values():
            await session.notifications.put(notice)


@app.on_event("startup")
async def startup():
    asyncio.create_task(_background_broadcaster())


@app.get("/events")
async def events(session_id: str = "default"):
    """GET — 服务端主动推送（等价于 MCP 的 GET /mcp）。

    客户端挂一个 GET 长连接，服务端随时往里推通知。
    这就是 MCP Streamable HTTP 里 "服务端主动推送" 的机制。
    """
    session = sessions.setdefault(session_id, ClientSession())

    async def generate():
        while True:
            try:
                msg = await asyncio.wait_for(session.notifications.get(), timeout=30)
                yield f"data: {msg}\n\n"
            except asyncio.TimeoutError:
                yield ":keepalive\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat")
async def chat(body: dict, session_id: str = "default"):
    """POST — 客户端请求，服务端流式回复（等价于 MCP 的 POST /mcp）。

    客户端发一条消息，服务端逐 chunk 流式回复。
    同时，服务端可能往 GET 流推一条通知（模拟 MCP 里的 progress 通知）。
    """
    session = sessions.setdefault(session_id, ClientSession())
    user_text = body.get("text", "")

    async def generate():
        # 模拟：处理请求的同时往 GET 流推一个 progress 通知
        await session.notifications.put(json.dumps({
            "type": "progress",
            "message": f"正在处理: {user_text}",
            "ts": time.time(),
        }, ensure_ascii=False))

        # 流式回复
        reply = f"你说的是「{user_text}」，我收到了。"
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

async def run_client():
    """演示：同时保持 GET 订阅 + POST 请求。"""
    session_id = "demo-session"

    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=None) as client:
        # ① 开一个 GET 长连接（订阅服务端通知）
        #    等价于 MCP 客户端的 GET /mcp?session_id=xxx
        async def listen_notifications():
            print("[通知监听] 已连接\n")
            async with client.stream("GET", f"/events?session_id={session_id}") as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        data = json.loads(line.removeprefix("data:").strip())
                        print(f"  🔔 通知: {data['message']}")

        # ② POST 请求（流式对话）
        #    等价于 MCP 客户端的 POST /mcp
        async def do_chat():
            for text in ["你好", "今天天气怎么样", "再见"]:
                print(f"\n→ 发送: {text}")
                async with client.stream(
                    "POST", f"/chat?session_id={session_id}",
                    json={"text": text},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            data = json.loads(line.removeprefix("data:").strip())
                            if data.get("done"):
                                print()
                            else:
                                print(data["ch"], end="", flush=True)
                await asyncio.sleep(2)

        # 同时跑两个任务
        notify_task = asyncio.create_task(listen_notifications())
        chat_task = asyncio.create_task(do_chat())

        await chat_task
        notify_task.cancel()
        print("\n[演示结束]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--client", action="store_true")
    args = parser.parse_args()

    if args.client:
        asyncio.run(run_client())
    else:
        import uvicorn
        uvicorn.run(app, port=8000)
