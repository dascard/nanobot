"""
FastAPI 应用入口点。
只负责组装路由、日志配置和中间件。
"""

import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import LOG_DIR, LOG_LEVEL
from core.database import init_db
from api.routes import router as api_router

# ── 日志配置 (支持持久化分割) ──
import os

os.makedirs(LOG_DIR, exist_ok=True)
handler = RotatingFileHandler(
    filename=f"{LOG_DIR}/nanobot.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=5,
    encoding="utf-8",
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
    digest_thread = None
    digest_stop_event = None

    from config import DAILY_DIGEST_ENABLED
    if DAILY_DIGEST_ENABLED:
        from core.daily_digest import daily_digest_scheduler
        digest_stop_event = threading.Event()
        digest_thread = threading.Thread(
            target=daily_digest_scheduler,
            args=(digest_stop_event,),
            daemon=True,
            name="daily-digest-scheduler",
        )
        digest_thread.start()
        logger.info("Daily digest scheduler initialized.")

    # Scheduled task runner (push notifications to QQ)
    from core.daily_digest import scheduled_task_runner
    task_stop_event = threading.Event()
    task_thread = threading.Thread(
        target=scheduled_task_runner,
        args=(task_stop_event,),
        daemon=True,
        name="scheduled-task-runner",
    )
    task_thread.start()
    logger.info("Scheduled task runner initialized.")

    # Pre-load sentinel model at startup (not lazily on first classify)
    try:
        from clients.classifier_client import Guardrail
        Guardrail._load_sentinel()
    except Exception as e:
        logger.warning(f"Sentinel pre-load failed (will retry on first classify): {e}")

    # Initialize KT Framework bridge (replaces old manual controller)
    from nanobot_kt.bridge import init_bridge, shutdown_bridge
    bridge = await init_bridge()
    logger.info(f"KT Agent initialized via bridge.")
    # Also init legacy controller for endpoints that still use SQLiteMemory
    from api.routes import init_legacy_memory
    init_legacy_memory()
    yield
    logger.info("Shutting down Nanobot Server Gateway...")
    if digest_stop_event is not None:
        digest_stop_event.set()
    if digest_thread is not None:
        digest_thread.join(timeout=5)
    await shutdown_bridge()


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
