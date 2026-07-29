"""FastAPI lifespan 启动编排。"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from bootstrap.application_modules import (
    ApplicationModuleDependencies,
    build_application_modules,
)
from core.modules import (
    CompositionRoot,
    CompositionState,
    ModuleRuntimeContext,
)
from core.database import init_db
from core.runtime_health import (
    mark_prompt_runtime_ready,
    mark_starting,
    mark_startup_complete,
    mark_stopping,
)
from core.sqlite_maintenance import (
    start_sqlite_maintenance,
    stop_sqlite_maintenance,
)
from core.retrieval import (
    start_retrieval_runtime,
    stop_retrieval_runtime,
)
from core.proactive.runtime_identity import (
    start_proactive_runtime,
    stop_proactive_runtime,
)
from core.sandbox.admin_operations import (
    start_sandbox_admin_operations,
    stop_sandbox_admin_operations,
)
from core.telemetry.runtime import (
    start_telemetry_runtime,
    stop_telemetry_runtime,
)

from bootstrap.network_check import run_startup_network_check
from bootstrap.model_runtime import start_model_runtime, stop_model_runtime
from bootstrap.prompt_runtime import init_prompt_runtimes
from bootstrap.provider_migration import run_provider_migration
from bootstrap.schedulers import start_schedulers


async def init_bridge() -> Any:
    from nanobot_kt.bridge import init_bridge as _init_bridge

    return await _init_bridge()


async def shutdown_bridge() -> None:
    from nanobot_kt.bridge import shutdown_bridge as _shutdown_bridge

    from core.agent_link.runtime import shutdown_agent_link_runtime

    try:
        await shutdown_agent_link_runtime()
    finally:
        await _shutdown_bridge()


def bind_agent_runtime(bridge: object) -> None:
    """把 KT Adapter 绑定到框架无关 Gateway Port。"""

    from core.agent_link.runtime import get_agent_link_runtime
    from core.agent_runtime.gateway import bind_agent_runtime as _bind
    from nanobot_kt.agent_link_adapter import KtAgentLinkChatAdapter
    from nanobot_kt.bridge import NanobotBridge
    from nanobot_kt.research_runtime import create_research_runtime

    _bind(
        gateway_provider=lambda: bridge,
        isolated_gateway_factory=NanobotBridge,
        research_runtime_factory=create_research_runtime,
    )
    get_agent_link_runtime().bind_chat_port(
        KtAgentLinkChatAdapter(bridge)
    )


def clear_agent_runtime_bindings() -> None:
    from core.agent_runtime.gateway import (
        clear_agent_runtime_bindings as _clear,
    )

    _clear()


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


def stop_schedulers(handles: object | None) -> None:
    """停止当前 delivery 模块持有的调度器集合。"""

    if handles is not None:
        handles.stop_all()


async def close_push_session() -> None:
    """关闭 daily digest 复用的模块级推送会话。"""

    from core.daily_digest import close_push_session as _close_push_session

    await _close_push_session()


def _application_module_dependencies() -> ApplicationModuleDependencies:
    """在每次 lifespan 启动时解析 façade，保留测试 monkeypatch 接缝。"""

    return ApplicationModuleDependencies(
        init_db=init_db,
        start_sqlite_maintenance=start_sqlite_maintenance,
        stop_sqlite_maintenance=stop_sqlite_maintenance,
        start_retrieval_runtime=start_retrieval_runtime,
        stop_retrieval_runtime=stop_retrieval_runtime,
        start_proactive_runtime=start_proactive_runtime,
        stop_proactive_runtime=stop_proactive_runtime,
        start_telemetry_runtime=start_telemetry_runtime,
        stop_telemetry_runtime=stop_telemetry_runtime,
        start_sandbox_admin_operations=lambda testing: (
            start_sandbox_admin_operations(testing=testing)
        ),
        stop_sandbox_admin_operations=stop_sandbox_admin_operations,
        validate_sandbox_asset_token_config=(
            validate_sandbox_asset_token_config
        ),
        run_provider_migration=run_provider_migration,
        start_model_runtime=start_model_runtime,
        stop_model_runtime=stop_model_runtime,
        init_prompt_runtimes=init_prompt_runtimes,
        mark_prompt_runtime_ready=mark_prompt_runtime_ready,
        start_schedulers=lambda testing, logger: start_schedulers(
            testing=testing,
            logger=logger,
        ),
        stop_schedulers=stop_schedulers,
        init_new_api_session=init_new_api_session,
        shutdown_new_api_session=shutdown_new_api_session,
        run_startup_network_check=lambda logger, session: (
            run_startup_network_check(logger, session=session)
        ),
        init_bridge=init_bridge,
        shutdown_bridge=shutdown_bridge,
        bind_agent_runtime=bind_agent_runtime,
        clear_agent_runtime_bindings=clear_agent_runtime_bindings,
        init_legacy_memory=init_legacy_memory,
        close_push_session=close_push_session,
    )


@asynccontextmanager
async def lifespan(app: Any):
    logger = logging.getLogger("nanobot")
    testing = os.environ.get("NANOBOT_TESTING") == "1"

    logger.info("Starting Nanobot Server Gateway...")
    mark_starting(testing=testing)
    app.state.bridge = None
    app.state.new_api_session = None
    root = CompositionRoot(
        build_application_modules(_application_module_dependencies())
    )
    app.state.composition_root = root
    try:
        await root.start(ModuleRuntimeContext(
            application=app,
            testing=testing,
            logger=logger,
        ))
        mark_startup_complete()
        yield
    finally:
        mark_stopping()
        logger.info("Shutting down Nanobot Server Gateway...")
        try:
            if root.state is CompositionState.RUNNING:
                await root.stop()
        finally:
            app.state.bridge = None
            app.state.new_api_session = None
            app.state.composition_root = None
