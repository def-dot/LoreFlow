"""Redis 工具"""
import redis.asyncio as redis

from app.core.config import settings
from app.core.logging import get_logger

_pool: redis.ConnectionPool | None = None

logger = get_logger(__name__)


def redis_client() -> redis.Redis:
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(
            settings.REDIS_URL, max_connections=50, decode_responses=True
        )
    return redis.Redis(connection_pool=_pool)
