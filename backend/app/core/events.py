"""跨 worker 事件通知 — Redis pub/sub。

多 worker 部署时，MCP 连接 / 插件注册表是进程内存，单 worker 变更后
需要通知其他 worker 重载。发布方写完共享存储（mcp.json / plugins 目录）
后调 publish_reload，各 worker 收到消息后执行对应 handler。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

CHANNEL = "loreflow:registry:reload"

_handlers: dict[str, Callable] = {}
_task: asyncio.Task[None] | None = None


def on_reload(kind: str):
    """注册重载处理器：收到 kind 对应的事件后调用。"""
    def deco(fn):
        _handlers[kind] = fn
        return fn
    return deco


async def publish_reload(kind: str) -> None:
    """发布重载事件（fire-and-forget，失败不影响主流程）。"""
    try:
        await redis_client().publish(CHANNEL, json.dumps({"kind": kind}))
    except Exception:
        logger.warning("[events] 发布重载事件失败 kind=%s", kind, exc_info=True)


async def _listen() -> None:
    pubsub = redis_client().pubsub()
    await pubsub.subscribe(CHANNEL)
    logger.info("[events] 已订阅重载频道 %s", CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            try:
                data = json.loads(msg["data"])
            except Exception:
                continue
            handler = _handlers.get(data.get("kind", ""))
            if handler is None:
                continue
            try:
                result = handler()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("[events] 重载处理失败 kind=%s", data.get("kind"))
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(CHANNEL)


async def start_listener() -> None:
    global _task
    _task = asyncio.create_task(_listen())


async def stop_listener() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
