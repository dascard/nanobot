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


def _startup_network_check(logger):
    """启动时探测关键后端连通性，结果记入日志。"""
    import urllib.request, json, time, os, subprocess
    # 打印 git commit 版本
    try:
        project_root = os.path.dirname(os.path.abspath(__file__))
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                      cwd=project_root,
                                      text=True, stderr=subprocess.DEVNULL).strip()
        dt = subprocess.check_output(["git", "log", "-1", "--format=%ci", "--date=short"],
                                     cwd=project_root,
                                     text=True, stderr=subprocess.DEVNULL).strip()[:10]
        logger.info("[startup] server version=%s date=%s", sha, dt)
    except Exception:
        logger.info("[startup] server version=unknown")

    # 打印 Admin WebUI Token
    from config import NANOBOT_ADMIN_TOKEN
    logger.info("[startup] ========================================")
    logger.info("[startup] Admin WebUI Token: %s", NANOBOT_ADMIN_TOKEN)
    logger.info("[startup] 访问 http://host:8000 并输入此 Token 登录")
    logger.info("[startup] ========================================")

    targets = {}

    # 构建显式代理 opener——urlopen(url_str) 不自动走环境代理
    proxy_url = os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY") or ""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(
        {"http": proxy_url, "https": proxy_url})) if proxy_url else urllib.request.build_opener()

    def _fetch(url, timeout):
        req = urllib.request.Request(url, headers={"User-Agent": "Nanobot/1.0"})
        return opener.open(req, timeout=timeout)

    # 1. LLM API (new-api) — 内网，不走代理
    base = os.environ.get("NEW_API_BASE_URL", "http://10.60.42.158:9000/v1")
    key = os.environ.get("NEW_API_KEY", "")
    t0 = time.time()
    try:
        req = urllib.request.Request(f"{base}/models",
            headers={"Authorization": f"Bearer {key}"} if key else {})
        with urllib.request.build_opener().open(req, timeout=5) as r:
            n = len(json.loads(r.read()).get("data", []))
        targets["llm_api"] = f"OK ({n} models, {time.time()-t0:.1f}s)"
    except Exception as e:
        targets["llm_api"] = f"FAIL: {type(e).__name__}: {e}"

    # 2. Qwen classifier — 内网，不走代理
    qwen = os.environ.get("CLASSIFIER_API_URL", "http://10.60.42.158:9999/v1")
    t0 = time.time()
    try:
        with urllib.request.build_opener().open(f"{qwen}/models", timeout=3) as r:
            targets["qwen"] = f"OK ({time.time()-t0:.1f}s)"
    except Exception as e:
        targets["qwen"] = f"FAIL: {type(e).__name__}: {e}"

    # 3. DuckDuckGo — 走代理
    t0 = time.time()
    try:
        with _fetch("https://duckduckgo.com", 5) as r:
            targets["ddg"] = f"OK ({r.status}, {time.time()-t0:.1f}s)"
    except Exception as e:
        targets["ddg"] = f"FAIL ({time.time()-t0:.1f}s): {type(e).__name__}: {e}"

    # 4. RSS — 走代理
    t0 = time.time()
    try:
        with _fetch("https://www.reddit.com/r/LocalLLaMA/.rss", 5) as r:
            targets["rss"] = f"OK ({r.status}, {time.time()-t0:.1f}s)"
    except Exception as e:
        targets["rss"] = f"FAIL ({time.time()-t0:.1f}s): {type(e).__name__}: {e}"

    # 汇总
    ok = sum(1 for v in targets.values() if v.startswith("OK"))
    fail = len(targets) - ok
    logger.info(f"[NetworkCheck] {ok}/{len(targets)} reachable:")
    for name, status in targets.items():
        logger.info(f"  {name}: {status}")
    if fail:
        logger.warning(f"[NetworkCheck] {fail} backends unreachable — news_search may be slow")


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
    learner_thread = None
    learner_stop_event = None

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

    # ── 群聊表达/黑话自动学习 ──
    from core.expression_learner import expression_learner_scheduler
    learner_stop_event = threading.Event()
    learner_thread = threading.Thread(
        target=expression_learner_scheduler,
        args=(learner_stop_event,),
        daemon=True,
        name="expression-learner",
    )
    learner_thread.start()

    # ── 启动网络连通性检测 ──
    _startup_network_check(logger)

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
    if learner_stop_event is not None:
        learner_stop_event.set()
    if learner_thread is not None:
        learner_thread.join(timeout=5)
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

from api.admin_routes import router as admin_router
app.include_router(admin_router)

from fastapi.staticfiles import StaticFiles
from pathlib import Path as _Path
_webui_dist = _Path(__file__).parent / "webui" / "dist"
if _webui_dist.exists():
    app.mount("/", StaticFiles(directory=str(_webui_dist), html=True), name="webui")
