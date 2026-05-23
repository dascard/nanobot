"""启动日志配置。"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

from config import LOG_DIR, LOG_LEVEL


def configure_logging() -> logging.Logger:
    """配置 nanobot 根 logger，并返回同名 logger。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger("nanobot")
    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        filename=f"{LOG_DIR}/nanobot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)
    return logger
