"""健康检查 — /api/v1/health（不走统一信封，便于部署脚本探活）"""

from fastapi import APIRouter
from sqlalchemy import text

from app.core import database
from app.core.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)


@router.get("/health")
async def health() -> dict:
    """探活接口：deploy.sh 轮询它判断服务是否就绪。

    DB 不可达时仍返回 200（带降级 payload），避免探活失败导致容器
    被反复重启——数据库问题由业务接口暴露。
    """
    try:
        async with database.AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        logger.warning("Health check: database unreachable: %s", exc)
        db_status = "error"
    return {"status": "ok", "database": db_status}
