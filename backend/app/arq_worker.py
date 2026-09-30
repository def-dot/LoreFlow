"""Arq worker — 文档解析后台任务。"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from arq.typing import WorkerSettingsType

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── 分布式锁装饰器 ─────────────────────────────────────────────────


def with_lock(func):
    """Arq job 分布式锁：Redis SET NX + 后台续期。"""

    @functools.wraps(func)
    async def wrapper(ctx: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        from app.utils.arq import LOCK_RENEW, LOCK_TTL, doc_lock_key

        redis = ctx.get("redis")
        if redis is None:
            return await func(ctx, *args, **kwargs)

        document_id = args[0] if args else kwargs.get("document_id")
        lock_key = doc_lock_key(document_id)

        # 抢锁
        acquired = await redis.set(lock_key, "1", ex=LOCK_TTL, nx=True)
        if not acquired:
            logger.warning("lock not acquired for doc %d, skip", document_id)
            return None

        renew_task = asyncio.create_task(_renew_lock(redis, lock_key, LOCK_TTL, LOCK_RENEW))
        try:
            return await func(ctx, *args, **kwargs)
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass
            await redis.delete(lock_key)

    return wrapper


async def _renew_lock(redis: Any, key: str, ttl: int, interval: int) -> None:
    """后台任务：定期续期锁。"""
    try:
        while True:
            await asyncio.sleep(interval)
            await redis.expire(key, ttl)
    except asyncio.CancelledError:
        pass


# ── Job 函数 ────────────────────────────────────────────────────────


@with_lock
async def parse_func(ctx: dict[str, Any], document_id: int) -> None:
    """文档解析入口。"""
    from app.services.knowledge import parse_document

    logger.info("parse_func: doc %d started", document_id)
    await parse_document(document_id)
    logger.info("parse_func: doc %d finished", document_id)


async def reconcile_cron(ctx: dict[str, Any]) -> None:
    """定时扫描卡住的文档并重新入队。"""
    from app.services.knowledge import reconcile_stuck

    redis = ctx.get("redis")
    await reconcile_stuck(redis)


# ── Worker Settings ─────────────────────────────────────────────────


class ParseWorkerSettings:
    """文档解析专用 worker。"""
    functions: list = [parse_func]
    queue_name: str = settings.PARSE_QUEUE_NAME
    max_jobs: int = settings.PARSE_MAX_JOBS
    job_timeout: int = settings.PARSE_JOB_TIMEOUT
    max_tries: int = 1
    allow_abort_jobs: bool = True
    redis_settings: RedisSettings = RedisSettings.from_dsn(settings.REDIS_URL)


class DefaultWorkerSettings:
    """默认 worker（定时任务）。"""
    functions: list = []
    cron_jobs: list = [
        cron(reconcile_cron, second={0, 10, 20, 30, 40, 50}),
    ]
    queue_name: str = settings.DEFAULT_QUEUE_NAME
    max_jobs: int = settings.DEFAULT_MAX_JOBS
    redis_settings: RedisSettings = RedisSettings.from_dsn(settings.REDIS_URL)