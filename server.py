"""
FastAPI 应用入口点。
只负责组装路由、日志配置和中间件。
"""
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import LOG_DIR, LOG_LEVEL
from database import init_db
from routes import router as api_router

# ── 日志配置 (支持持久化分割) ──
import os
os.makedirs(LOG_DIR, exist_ok=True)
handler = RotatingFileHandler(
    filename=f"{LOG_DIR}/nanobot.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding="utf-8"
)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)

logger = logging.getLogger("nanobot")
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
if not logger.handlers:
    logger.addHandler(handler)
    # 也保留一份控制台输出供 Docker Logs 查看
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)


# ── 生命周期 ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Nanobot Server Gateway...")
    init_db()
    logger.info("Database initialized.")
    yield
    logger.info("Shutting down Nanobot Server Gateway...")


# ── 应用初始化 ──
app = FastAPI(title="Nanobot Self-Evolution Gateway", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
