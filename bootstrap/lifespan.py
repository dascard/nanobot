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

    return await _init_bridge(
        selection_policy=build_agent_runtime_selection_policy(),
    )


def build_agent_runtime_selection_policy():
    """从仅启动期可变的配置构建冻结 Runtime 选择策略。"""

    from core.agent_runtime import (
        AgentRuntimeKind,
        AgentRuntimeSelectionPolicy,
        parse_runtime_scope_ids,
    )
    from core.settings_service import settings

    return AgentRuntimeSelectionPolicy(
        default_kind=AgentRuntimeKind(
            settings.get_str("agent.runtime.default", "native").strip().lower()
        ),
        kt_enabled=settings.get_bool("agent.runtime.kt_enabled", False),
        kt_percentage_basis_points=settings.get_int(
            "agent.runtime.kt_rollout_basis_points",
            0,
        ),
        kt_session_ids=parse_runtime_scope_ids(
            settings.get_str("agent.runtime.kt_session_allowlist", "")
        ),
    )


async def shutdown_bridge() -> None:
    from nanobot_kt.bridge import shutdown_bridge as _shutdown_bridge

    from core.agent_link.runtime import shutdown_agent_link_runtime

    try:
        await shutdown_agent_link_runtime()
    finally:
        await _shutdown_bridge()


def bind_agent_runtime(bridge: object) -> None:
    """把双 Runtime Bridge 绑定到框架无关 Gateway Port。"""

    from core import database
    from core.agent_link.runtime import get_agent_link_runtime
    from core.agent_runtime.gateway import bind_agent_runtime as _bind
    from core.agent_runtime.gateway import (
        clear_agent_runtime_bindings as _clear,
    )
    from core.scheduled_workflow_runtime import (
        bind_scheduled_workflow_callbacks,
        clear_scheduled_workflow_callbacks,
    )
    from core.media_preprocess_runtime import (
        bind_image_precache_port,
        clear_image_precache_port,
    )
    from nanobot_kt.agent_link_adapter import KtAgentLinkChatAdapter
    from nanobot_kt.bridge import NanobotBridge
    from bootstrap.research_runtime import build_research_runtime_factory
    from nanobot_kt.media_preprocess_adapter import KtImagePrecacheAdapter
    from nanobot_kt.scheduled_workflow_adapter import (
        KtScheduledWorkflowCallbacks,
    )

    try:
        isolated_gateway_factory = getattr(
            bridge,
            "create_isolated_bridge",
            None,
        )
        if not callable(isolated_gateway_factory):
            isolated_gateway_factory = NanobotBridge
        _bind(
            gateway_provider=lambda: bridge,
            isolated_gateway_factory=isolated_gateway_factory,
            research_runtime_factory=build_research_runtime_factory(
                isolated_gateway_factory
            ),
        )
        bind_image_precache_port(KtImagePrecacheAdapter())
        bind_scheduled_workflow_callbacks(
            lambda: KtScheduledWorkflowCallbacks(
                session_factory=database.SessionLocal,
            )
        )
        get_agent_link_runtime().bind_chat_port(
            KtAgentLinkChatAdapter(bridge)
        )
    except BaseException:
        clear_scheduled_workflow_callbacks()
        clear_image_precache_port()
        _clear()
        raise


def clear_agent_runtime_bindings() -> None:
    from core.agent_runtime.gateway import (
        clear_agent_runtime_bindings as _clear,
    )
    from core.scheduled_workflow_runtime import (
        clear_scheduled_workflow_callbacks,
    )
    from core.media_preprocess_runtime import clear_image_precache_port

    clear_scheduled_workflow_callbacks()
    clear_image_precache_port()
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
    from config import log_agent_link_token_configuration

    logger = logging.getLogger("nanobot")
    testing = os.environ.get("NANOBOT_TESTING") == "1"

    logger.info("Starting Nanobot Server Gateway...")
    log_agent_link_token_configuration(logger)
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
