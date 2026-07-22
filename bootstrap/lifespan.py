"""FastAPI lifespan 启动编排。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from core.database import init_db
from core.runtime_health import (
    mark_prompt_runtime_ready,
    mark_starting,
    mark_startup_complete,
    mark_stopping,
)
from core.sqlite_maintenance import (
    SQLiteMaintenanceWorker,
    start_sqlite_maintenance,
    stop_sqlite_maintenance,
)
from core.retrieval import (
    RerankerExecutorPort,
    start_retrieval_runtime,
    stop_retrieval_runtime,
)
from core.proactive.runtime_identity import (
    start_proactive_runtime,
    stop_proactive_runtime,
)
from core.sandbox.admin_operations import (
    SandboxAdminOperationRunner,
    start_sandbox_admin_operations,
    stop_sandbox_admin_operations,
)

from bootstrap.network_check import run_startup_network_check
from bootstrap.model_runtime import start_model_runtime, stop_model_runtime
from bootstrap.prompt_runtime import init_prompt_runtimes
from bootstrap.provider_migration import run_provider_migration
from bootstrap.schedulers import SchedulerHandles, start_schedulers


async def init_bridge() -> Any:
    from nanobot_kt.bridge import init_bridge as _init_bridge

    return await _init_bridge()


async def shutdown_bridge() -> None:
    from nanobot_kt.bridge import shutdown_bridge as _shutdown_bridge

    await _shutdown_bridge()


async def init_new_api_session() -> Any:
    import aiohttp
    from clients.new_api_client import NewAPIClient

    session = aiohttp.ClientSession()
    NewAPIClient.set_shared_session(session)
    return session


async def shutdown_new_api_session(session: Any) -> None:
    from clients.new_api_client import NewAPIClient

    NewAPIClient.set_shared_session(None)
    if session is not None and not getattr(session, "closed", False):
        await session.close()


def init_legacy_memory() -> None:
    from api.routes import init_legacy_memory as _init_legacy_memory

    _init_legacy_memory()


def validate_sandbox_asset_token_config() -> None:
    """Sandbox 启用时在启动阶段校验资产 Token 密钥。"""

    from core import database
    from core.asset_tokens import signer_from_settings
    from core.sandbox.tool_service import resolve_sandbox_setting

    db = database.SessionLocal()
    try:
        if bool(resolve_sandbox_setting(db, "sandbox.enabled", False)):
            signer_from_settings(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: Any):
    logger = logging.getLogger("nanobot")
    testing = os.environ.get("NANOBOT_TESTING") == "1"
    scheduler_handles: SchedulerHandles | None = None
    new_api_session: Any | None = None
    sqlite_maintenance: SQLiteMaintenanceWorker | None = None
    retrieval_executor: RerankerExecutorPort | None = None
    proactive_runtime_started = False
    sandbox_admin_runner: SandboxAdminOperationRunner | None = None

    logger.info("Starting Nanobot Server Gateway...")
    mark_starting(testing=testing)
    app.state.bridge = None
    app.state.new_api_session = None
    bridge_initialized = False
    model_runtime_started = False
    try:
        init_db()
        logger.info("Database initialized.")
        sandbox_admin_runner = start_sandbox_admin_operations(testing=testing)
        sqlite_maintenance = start_sqlite_maintenance()
        validate_sandbox_asset_token_config()
        run_provider_migration()
        start_model_runtime()
        model_runtime_started = True
        retrieval_executor = start_retrieval_runtime()
        start_proactive_runtime()
        proactive_runtime_started = True
        init_prompt_runtimes(logger)
        mark_prompt_runtime_ready()
        scheduler_handles = start_schedulers(testing=testing, logger=logger)
        new_api_session = await init_new_api_session()
        app.state.new_api_session = new_api_session

        if not testing:
            await run_startup_network_check(logger, session=new_api_session)
            app.state.bridge = await init_bridge()
            bridge_initialized = True
            logger.info("KT Agent initialized via bridge.")
        else:
            logger.info("NANOBOT_TESTING=1: skipped network check and KT bridge init.")

        init_legacy_memory()
        mark_startup_complete()
        yield
    finally:
        mark_stopping()
        logger.info("Shutting down Nanobot Server Gateway...")
        if scheduler_handles is not None:
            scheduler_handles.stop_all()
        stop_sandbox_admin_operations(sandbox_admin_runner)
        if proactive_runtime_started:
            stop_proactive_runtime()
        if model_runtime_started:
            stop_model_runtime()
        try:
            if bridge_initialized:
                await shutdown_bridge()
        finally:
            await shutdown_new_api_session(new_api_session)
            # 关闭 daily_digest 模块级 push session（H7 复用单例的清理）
            try:
                from core.daily_digest import close_push_session

                await close_push_session()
            except Exception:
                logger.debug("push session shutdown skipped", exc_info=True)
            app.state.bridge = None
            app.state.new_api_session = None
            stop_retrieval_runtime(retrieval_executor)
            stop_sqlite_maintenance(sqlite_maintenance)
