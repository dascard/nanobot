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


# ── 启动迁移：provider 旧名 → canonical 名 ──
def _run_provider_migration():
    """幂等迁移：重命名 DB 中旧 provider key / catalog key / route provider value。

    仅当旧名存在且 canonical 名不存在时才迁移，绝不覆盖用户显式配置。
    """
    import logging
    logger = logging.getLogger("nanobot.migration")

    from core.database import SessionLocal, SystemSetting
    from core.route_metadata import PROVIDER_ALIASES, canonical_provider_id, normalize_base_url
    from config import CLASSIFIER_API_URL, IMAGE_SUMMARY_API_URL

    db = SessionLocal()
    migrated: list[str] = []
    try:
        # 1. 重命名 provider key（仅当 canonical 不存在时复制）
        for old_pid, new_pid in PROVIDER_ALIASES.items():
            old_prefix = f"model.providers.{old_pid}."
            new_prefix = f"model.providers.{new_pid}."
            old_rows = db.query(SystemSetting).filter(
                SystemSetting.key.like(f"{old_prefix}%")
            ).all()
            for old_row in old_rows:
                new_key = old_row.key.replace(old_prefix, new_prefix, 1)
                existing = db.query(SystemSetting).filter(
                    SystemSetting.key == new_key
                ).first()
                if not existing:
                    new_row = SystemSetting(
                        key=new_key, value=old_row.value,
                        description=f"{old_row.description or ''} (migrated from {old_pid})",
                    )
                    db.add(new_row)
                    migrated.append(f"{old_row.key} → {new_key}")
                    logger.info("Provider key migrated: %s → %s", old_row.key, new_key)
        if migrated:
            db.commit()

        # 2. 重命名 route provider value（仅当值为旧名时）
        route_pv_rows = db.query(SystemSetting).filter(
            SystemSetting.key.like("model.route.%.provider")
        ).all()
        route_migrated = False
        for row in route_pv_rows:
            new_val = canonical_provider_id(row.value or "")
            if new_val != row.value:
                logger.info("Route provider value migrated: %s = %s → %s",
                            row.key, row.value, new_val)
                row.value = new_val
                route_migrated = True
        if route_migrated:
            db.commit()

        # 3. 重命名 catalog key
        for old_pid, new_pid in PROVIDER_ALIASES.items():
            old_cat_key = f"model.catalog.{old_pid}"
            new_cat_key = f"model.catalog.{new_pid}"
            old_cat = db.query(SystemSetting).filter(
                SystemSetting.key == old_cat_key
            ).first()
            if old_cat:
                existing = db.query(SystemSetting).filter(
                    SystemSetting.key == new_cat_key
                ).first()
                if not existing:
                    old_cat.key = new_cat_key
                    old_cat.description = (old_cat.description or "") + f" (migrated from {old_pid})"
                    migrated.append(f"{old_cat_key} → {new_cat_key}")
                    logger.info("Catalog key migrated: %s → %s", old_cat_key, new_cat_key)
        if migrated:
            db.commit()

        # 4. 检测合并端点：IMAGE_SUMMARY_API_URL == CLASSIFIER_API_URL 时
        #    sticker_describe.provider 应指向 local_llama（仅当当前值为旧名/空时）
        normalized_classifier = normalize_base_url(str(CLASSIFIER_API_URL or ""))
        normalized_vision = normalize_base_url(str(IMAGE_SUMMARY_API_URL or ""))
        if normalized_vision and normalized_vision == normalized_classifier:
            sp_row = db.query(SystemSetting).filter(
                SystemSetting.key == "model.route.sticker_describe.provider"
            ).first()
            current_val = (sp_row.value or "").strip() if sp_row else ""
            if not current_val or current_val in ("vision_qwen", "local_vision"):
                if sp_row:
                    sp_row.value = "local_llama"
                    logger.info("Merged endpoint: sticker_describe.provider → local_llama (was %s)", current_val)
                else:
                    db.add(SystemSetting(
                        key="model.route.sticker_describe.provider",
                        value="local_llama",
                        description="sticker_describe provider (merged endpoint detected)",
                    ))
                    logger.info("Merged endpoint: created sticker_describe.provider = local_llama")
                db.commit()

        # 5. 种子 env→DB（仅当 DB 无对应行时）
        import os as _os
        for pid, env_url, env_key in [
            ("newapi", "NEW_API_BASE_URL", "NEW_API_KEY"),
            ("local_llama", "CLASSIFIER_API_URL", None),
        ]:
            base_key = f"model.providers.{pid}.base_url"
            existing = db.query(SystemSetting).filter(
                SystemSetting.key == base_key
            ).first()
            if not existing:
                url = _os.environ.get(env_url, "")
                if url:
                    db.add(SystemSetting(
                        key=base_key, value=url,
                        description=f"provider {pid} base_url (seeded from env)",
                    ))
                    logger.info("Env seeded: %s = %s", base_key, url[:80])
            if env_key:
                api_key = f"model.providers.{pid}.api_key"
                existing_key = db.query(SystemSetting).filter(
                    SystemSetting.key == api_key
                ).first()
                if not existing_key:
                    key_val = _os.environ.get(env_key, "")
                    if key_val:
                        db.add(SystemSetting(
                            key=api_key, value=key_val,
                            description=f"provider {pid} api_key (seeded from env)",
                        ))
                        logger.info("Env seeded: %s (value hidden)", api_key)
        db.commit()

        if migrated:
            logger.info("Provider migration summary: %d items", len(migrated))
        else:
            logger.info("Provider migration: nothing to migrate")
    except Exception as e:
        logger.warning("Provider migration failed (non-fatal): %s", e)
        db.rollback()
    finally:
        db.close()
        try:
            from core.settings_service import settings
            settings.invalidate()
        except Exception:
            pass

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
    from config import NANOBOT_API_TOKEN, NANOBOT_ADMIN_TOKEN
    logger.info("[startup] ========================================")
    logger.info("[startup] Push API auth: %s", "enabled" if NANOBOT_API_TOKEN else "disabled")
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
    _run_provider_migration()
    # 从 prompts.default 初始化缺失的运行时模板到 data/prompts
    try:
        from core.prompts.manager import PromptManager
        init_result = PromptManager.init_runtime_dir()
        if init_result["copied"]:
            logger.info("[PromptManager] initialized %d templates from %s → %s",
                        len(init_result["copied"]),
                        init_result["source_dir"],
                        init_result["runtime_dir"])
        else:
            logger.info("[PromptManager] runtime dir ready: %s", init_result["runtime_dir"])
    except Exception as e:
        logger.warning("[PromptManager] init_runtime_dir failed: %s", e)
    # 从 prompts.legacy.default/fragments 初始化缺失的运行时 fragment 到 data/prompt_fragments
    try:
        from core.legacy_prompt_runtime import init_legacy_prompt_runtime_dir
        legacy_result = init_legacy_prompt_runtime_dir()
        if legacy_result["copied"]:
            logger.info("[LegacyPrompt] initialized %d fragments from %s → %s",
                        len(legacy_result["copied"]),
                        legacy_result["source_dir"],
                        legacy_result["runtime_dir"])
        else:
            logger.info("[LegacyPrompt] runtime fragments ready: %s", legacy_result["runtime_dir"])
    except Exception as e:
        logger.warning("[LegacyPrompt] init_runtime_dir failed: %s", e)
    digest_thread = None
    digest_stop_event = None
    learner_thread = None
    learner_stop_event = None

    testing = os.environ.get("NANOBOT_TESTING") == "1"
    from config import DAILY_DIGEST_ENABLED
    if DAILY_DIGEST_ENABLED and not testing:
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

    task_stop_event = None
    task_thread = None
    eval_sample_stop = None
    eval_sample_thread = None

    if not testing:
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

        # ── Eval 自动采样 ──
        from core.eval_sampling.scheduler import eval_sampling_scheduler
        eval_sample_stop = threading.Event()
        eval_sample_thread = threading.Thread(
            target=eval_sampling_scheduler,
            args=(eval_sample_stop,),
            daemon=True,
            name="eval-sampling-scheduler",
        )
        eval_sample_thread.start()
        logger.info("Eval sampling scheduler initialized.")

        # ── 启动网络连通性检测 ──
        _startup_network_check(logger)

        # Initialize KT Framework bridge (replaces old manual controller)
        from nanobot_kt.bridge import init_bridge
        bridge = await init_bridge()
        app.state.bridge = bridge
        logger.info("KT Agent initialized via bridge.")
    else:
        logger.info("NANOBOT_TESTING=1: skipped schedulers, network check and KT bridge init.")
        app.state.bridge = None

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
    if task_stop_event is not None:
        task_stop_event.set()
    if task_thread is not None:
        task_thread.join(timeout=5)
    if eval_sample_stop is not None:
        eval_sample_stop.set()
    if eval_sample_thread is not None:
        eval_sample_thread.join(timeout=5)
    if not testing:
        from nanobot_kt.bridge import shutdown_bridge
        await shutdown_bridge()
    app.state.bridge = None


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
from starlette.exceptions import HTTPException as StarletteHTTPException
from pathlib import Path as _Path


class SPAStaticFiles(StaticFiles):
    """为 WebUI 的前端路由提供 index.html 回退，同时保留 API 404。"""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or self._should_keep_not_found(path):
                raise
            return await super().get_response("index.html", scope)

    @staticmethod
    def _should_keep_not_found(path: str) -> bool:
        if path.startswith(("api/", "docs", "redoc", "openapi.json")):
            return True
        return bool(_Path(path).suffix)


_webui_dist = _Path(__file__).parent / "webui" / "dist"
if _webui_dist.exists():
    app.mount("/", SPAStaticFiles(directory=str(_webui_dist), html=True), name="webui")
