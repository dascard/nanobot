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


def init_legacy_memory() -> None:
    from api.routes import init_legacy_memory as _init_legacy_memory

    _init_legacy_memory()


@asynccontextmanager
async def lifespan(app: Any):
    logger = logging.getLogger("nanobot")
    testing = os.environ.get("NANOBOT_TESTING") == "1"
    scheduler_handles: SchedulerHandles | None = None

    logger.info("Starting Nanobot Server Gateway...")
    init_db()
    logger.info("Database initialized.")
    run_provider_migration()
    init_prompt_runtimes(logger)
    scheduler_handles = start_schedulers(testing=testing, logger=logger)

    if not testing:
        run_startup_network_check(logger)
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
        if not testing:
            await shutdown_bridge()
        app.state.bridge = None
