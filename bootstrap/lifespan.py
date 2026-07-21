"""FastAPI lifespan 启动编排。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from core.database import init_db

from bootstrap.network_check import run_startup_network_check
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

    logger.info("Starting Nanobot Server Gateway...")
    init_db()
    logger.info("Database initialized.")
    validate_sandbox_asset_token_config()
    run_provider_migration()
    init_prompt_runtimes(logger)
    scheduler_handles = start_schedulers(testing=testing, logger=logger)
    new_api_session = await init_new_api_session()
    app.state.new_api_session = new_api_session

    if not testing:
        await run_startup_network_check(logger, session=new_api_session)
        app.state.bridge = await init_bridge()
        logger.info("KT Agent initialized via bridge.")
    else:
        logger.info("NANOBOT_TESTING=1: skipped network check and KT bridge init.")
        app.state.bridge = None

    init_legacy_memory()
    try:
        yield
    finally:
        logger.info("Shutting down Nanobot Server Gateway...")
        if scheduler_handles is not None:
            scheduler_handles.stop_all()
        try:
            if not testing:
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
