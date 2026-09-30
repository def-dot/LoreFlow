"""Arq 任务队列 — 分队列入队工具。

- parse 队列：文档解析（enqueue_parse / cancel_parse）
- default 队列：默认任务

分布式锁机制：
  每个 job 被 worker 取走时，用 Redis SET NX 抢锁。
  reconcile 只回收锁已过期的 job，不会误杀其他机器上的 worker。
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.jobs import Job, JobStatus

from app.core.config import settings

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _pool


# ── 分布式锁 ────────────────────────────────────────────────────────

LOCK_TTL = 60        # 锁过期时间（秒），持锁 worker 宕机 60s 后自动释放
LOCK_RENEW = 20      # 续期间隔（秒）
_JOB_ID_PREFIX = "parse-doc-"


def doc_job_id(document_id: int) -> str:
    """文档解析 job 的固定 job_id。"""
    return f"{_JOB_ID_PREFIX}{document_id}"


def doc_lock_key(document_id: int) -> str:
    """文档解析的分布式锁 Redis key。"""
    return f"arq:lock:{doc_job_id(document_id)}"


# ── 入队 / 取消 ────────────────────────────────────────────────────

async def enqueue_parse(document_id: int) -> bool:
    """新文档入队（API 用）。已有同 ID job 则跳过去重。"""
    pool = await get_pool()
    job = await pool.enqueue_job(
        "parse_func",
        document_id,
        _job_id=doc_job_id(document_id),
        _queue_name=settings.PARSE_QUEUE_NAME,
    )
    if job is None:
        return False
    return True


async def delete_job(job_id: str) -> None:
    """删除 job 相关的所有 Redis key，确保能重新入队。"""
    pool = await get_pool()
    await pool.delete(
        f"arq:job:{job_id}",
        f"arq:result:{job_id}",
        f"arq:in-progress:{job_id}",
        f"arq:retry:{job_id}",
    )


async def cancel_parse(document_id: int) -> bool:
    """中止排队或运行中的解析任务。"""
    pool = await get_pool()
    job_id = doc_job_id(document_id)
    job = Job(job_id, pool, _queue_name=settings.PARSE_QUEUE_NAME)
    status = await job.status()
    if status in (JobStatus.not_found, JobStatus.complete):
        return False
    await job.abort(timeout=5)
    await delete_job(job_id)
    return True