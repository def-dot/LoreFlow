"""日志配置"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings


def setup_logging(log_file: str = "app.log", use_file: bool = True) -> None:
    """初始化应用日志 - 输出到控制台，可选轮转文件（logs/ 目录）。

    use_file: 是否写轮转文件（测试环境关闭，避免产生 logs/ 目录）。
    """
    log_level = settings.LOG_LEVEL.upper()
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if use_file:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / log_file,
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        level=log_level,
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger"""
    return logging.getLogger(name)
