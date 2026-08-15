"""现有运行时组件到显式 ``ApplicationModule`` 的生产 Adapter。

本模块只负责编排已有启动/停止 façade。FastAPI、KT 和具体资源类型不会进入
``core.modules`` 合同层；所有外部函数都由 composition root 创建时显式注入。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core.modules import (
    ApplicationModule,
    ModuleContributionRef,
    ModuleHealth,
    ModuleHealthCheck,
    ModuleManifest,
    ModuleRegistrationPort,
    ModuleRuntimeContext,
)


SyncAction = Callable[[], None]
StartResource = Callable[[], object | None]
StopResource = Callable[[object | None], None]
AsyncStartResource = Callable[[], Awaitable[object | None]]
AsyncStopResource = Callable[[object | None], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ApplicationModuleDependencies:
    """九个内建模块使用的显式启动依赖。"""

    init_db: SyncAction
    reconcile_skill_candidate_publications: Callable[[bool], None]
    start_sqlite_maintenance: StartResource
    stop_sqlite_maintenance: StopResource
    start_retrieval_runtime: StartResource
    stop_retrieval_runtime: StopResource
    start_proactive_runtime: SyncAction
    stop_proactive_runtime: SyncAction
    start_telemetry_runtime: StartResource
    stop_telemetry_runtime: StopResource
    start_sandbox_admin_operations: Callable[[bool], object | None]
    stop_sandbox_admin_operations: StopResource
    validate_sandbox_asset_token_config: SyncAction
    run_provider_migration: SyncAction
    start_model_runtime: SyncAction
    stop_model_runtime: SyncAction
    init_prompt_runtimes: Callable[[object], None]
    mark_prompt_runtime_ready: SyncAction
    start_schedulers: Callable[[bool, object, object], object | None]
    stop_schedulers: StopResource
    init_new_api_session: AsyncStartResource
    shutdown_new_api_session: AsyncStopResource
    run_startup_network_check: Callable[
        [object, object],
        Awaitable[None],
    ]
    init_bridge: AsyncStartResource
    shutdown_bridge: Callable[[], Awaitable[None]]
    bind_agent_runtime: Callable[[object], None]
    clear_agent_runtime_bindings: SyncAction
    init_legacy_memory: SyncAction
    close_push_session: Callable[[], Awaitable[None]]


def _manifest(
    module_id: str,
    *,
    domain: str,
    dependency: str | None,
    phase: int,
    contribution_kind: str,
    additional_contributions: tuple[ModuleContributionRef, ...] = (),
) -> ModuleManifest:
    dependencies = (dependency,) if dependency else ()
    contribution = ModuleContributionRef(
        kind=contribution_kind,
        contribution_id=module_id,
    )
    return ModuleManifest(
        module_id=module_id,
        version="1.0.0",
        owner="nanobot",
        domain=domain,
        required_modules=dependencies,
        provided_capabilities=(f"{module_id}.port",),
        contributions=(contribution, *additional_contributions),
        startup_phase=phase,
        shutdown_phase=phase,
        health_checks=("lifecycle",),
        readiness_checks=("lifecycle",),
        release_impacts=("runtime",),
    )


class _BuiltinModule:
    """内建模块的最小状态与 Manifest 公共实现。"""

    def __init__(self, manifest: ModuleManifest) -> None:
        self._manifest = manifest
        self._started = False

    def manifest(self) -> ModuleManifest:
        return self._manifest

    def register(self, builder: ModuleRegistrationPort) -> None:
        for contribution in self._manifest.contributions:
            builder.register(
                contribution.kind,
                contribution.contribution_id,
            )

    def health(self) -> ModuleHealth:
        return ModuleHealth(
            status="healthy" if self._started else "stopped",
            ready=self._started,
            checks=(
                ModuleHealthCheck(
                    name="lifecycle",
                    healthy=self._started,
                    detail_code="" if self._started else "not_started",
                ),
            ),
        )

    async def start(
        self,
        runtime_context: ModuleRuntimeContext,
    ) -> None:
        if self._started:
            raise RuntimeError(
                f"模块 {self._manifest.module_id} 已启动"
            )
        await self._start(runtime_context)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            await self._stop()
        finally:
            self._started = False

    async def _start(
        self,
        runtime_context: ModuleRuntimeContext,
    ) -> None:
        del runtime_context

    async def _stop(self) -> None:
        return None


def _raise_first(errors: list[BaseException]) -> None:
    if errors:
        raise errors[0]


class _MemoryRuntimeModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "memory.runtime",
            domain="memory",
            dependency=None,
            phase=10,
            contribution_kind="port",
            additional_contributions=(
                ModuleContributionRef(
                    kind="job",
                    contribution_id="runtime.job_kernel",
                ),
            ),
        ))
        self._dependencies = dependencies
        self._sqlite_maintenance: object | None = None
        self._retrieval_executor: object | None = None
        self._proactive_started = False
        self._application: object | None = None

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        from bootstrap.job_adapters import (
            build_job_lease_adapter_registry,
        )

        self._application = runtime_context.application
        registry = build_job_lease_adapter_registry()
        self._application.state.job_lease_adapters = registry
        try:
            self._dependencies.init_db()
            self._dependencies.reconcile_skill_candidate_publications(
                runtime_context.testing
            )
            self._sqlite_maintenance = (
                self._dependencies.start_sqlite_maintenance()
            )
            self._retrieval_executor = (
                self._dependencies.start_retrieval_runtime()
            )
            self._dependencies.start_proactive_runtime()
            self._proactive_started = True
        except BaseException:
            errors: list[BaseException] = []
            if self._retrieval_executor is not None:
                try:
                    self._dependencies.stop_retrieval_runtime(
                        self._retrieval_executor
                    )
                except BaseException as exc:
                    errors.append(exc)
                self._retrieval_executor = None
            if self._sqlite_maintenance is not None:
                try:
                    self._dependencies.stop_sqlite_maintenance(
                        self._sqlite_maintenance
                    )
                except BaseException as exc:
                    errors.append(exc)
                self._sqlite_maintenance = None
            self._application.state.job_lease_adapters = None
            self._application = None
            raise

    async def _stop(self) -> None:
        errors: list[BaseException] = []
        if self._proactive_started:
            try:
                self._dependencies.stop_proactive_runtime()
            except BaseException as exc:
                errors.append(exc)
            self._proactive_started = False
        if self._retrieval_executor is not None:
            try:
                self._dependencies.stop_retrieval_runtime(
                    self._retrieval_executor
                )
            except BaseException as exc:
                errors.append(exc)
            self._retrieval_executor = None
        if self._sqlite_maintenance is not None:
            try:
                self._dependencies.stop_sqlite_maintenance(
                    self._sqlite_maintenance
                )
            except BaseException as exc:
                errors.append(exc)
            self._sqlite_maintenance = None
        if self._application is not None:
            self._application.state.job_lease_adapters = None
            self._application = None
        _raise_first(errors)


class _SandboxControlPlaneModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "sandbox.control_plane",
            domain="sandbox",
            dependency="runtime.telemetry",
            phase=20,
            contribution_kind="endpoint",
            additional_contributions=(
                ModuleContributionRef(
                    kind="policy",
                    contribution_id="sandbox.access",
                ),
            ),
        ))
        self._dependencies = dependencies
        self._runner: object | None = None

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        self._runner = (
            self._dependencies.start_sandbox_admin_operations(
                runtime_context.testing
            )
        )
        try:
            self._dependencies.validate_sandbox_asset_token_config()
        except BaseException:
            self._dependencies.stop_sandbox_admin_operations(
                self._runner
            )
            self._runner = None
            raise

    async def _stop(self) -> None:
        self._dependencies.stop_sandbox_admin_operations(self._runner)
        self._runner = None


class _TelemetryModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "runtime.telemetry",
            domain="telemetry",
            dependency="memory.runtime",
            phase=15,
            contribution_kind="telemetry",
            additional_contributions=(
                ModuleContributionRef(
                    kind="metric",
                    contribution_id="runtime.telemetry_metrics",
                ),
            ),
        ))
        self._dependencies = dependencies
        self._runtime: object | None = None
        self._application: object | None = None

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        application = runtime_context.application
        if application is None:
            raise RuntimeError("runtime.telemetry 缺少 application")
        self._application = application
        self._runtime = self._dependencies.start_telemetry_runtime()
        application.state.telemetry_runtime = self._runtime

    async def _stop(self) -> None:
        self._dependencies.stop_telemetry_runtime(self._runtime)
        if self._application is not None:
            self._application.state.telemetry_runtime = None
        self._runtime = None
        self._application = None


class _ModelProviderModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "model.provider",
            domain="model",
            dependency="sandbox.control_plane",
            phase=30,
            contribution_kind="model_route",
        ))
        self._dependencies = dependencies
        self._runtime_started = False

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        del runtime_context
        self._dependencies.run_provider_migration()
        self._dependencies.start_model_runtime()
        self._runtime_started = True

    async def _stop(self) -> None:
        if self._runtime_started:
            self._dependencies.stop_model_runtime()
            self._runtime_started = False


class _PromptRuntimeModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "prompt.runtime",
            domain="prompt",
            dependency="model.provider",
            phase=40,
            contribution_kind="prompt",
            additional_contributions=(
                ModuleContributionRef(
                    kind="transform_hook",
                    contribution_id="prompt.contribution",
                ),
            ),
        ))
        self._dependencies = dependencies

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        logger = runtime_context.logger
        if logger is None:
            raise RuntimeError("prompt.runtime 缺少 logger")
        self._dependencies.init_prompt_runtimes(logger)
        self._dependencies.mark_prompt_runtime_ready()


class _ToolRuntimeModule(_BuiltinModule):
    def __init__(self) -> None:
        super().__init__(_manifest(
            "tool.runtime",
            domain="tool",
            dependency="prompt.runtime",
            phase=50,
            contribution_kind="tool",
        ))


class _DeliveryOutboundModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "delivery.outbound",
            domain="delivery",
            dependency="tool.runtime",
            phase=60,
            contribution_kind="job",
        ))
        self._dependencies = dependencies
        self._handles: object | None = None
        self._application: object | None = None

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        logger = runtime_context.logger
        if logger is None:
            raise RuntimeError("delivery.outbound 缺少 logger")
        application = runtime_context.application
        if application is None:
            raise RuntimeError("delivery.outbound 缺少 application")
        self._handles = self._dependencies.start_schedulers(
            runtime_context.testing,
            logger,
            application,
        )
        self._application = application
        application.state.scheduler_handles = self._handles

    async def _stop(self) -> None:
        errors: list[BaseException] = []
        if self._application is not None:
            self._application.state.scheduler_handles = None
            self._application = None
        try:
            self._dependencies.stop_schedulers(self._handles)
        except BaseException as exc:
            errors.append(exc)
        self._handles = None
        try:
            await self._dependencies.close_push_session()
        except BaseException as exc:
            errors.append(exc)
        _raise_first(errors)


class _AdminApiModule(_BuiltinModule):
    def __init__(self) -> None:
        super().__init__(_manifest(
            "admin.api",
            domain="admin",
            dependency="delivery.outbound",
            phase=70,
            contribution_kind="endpoint",
            additional_contributions=(
                ModuleContributionRef(
                    kind="endpoint",
                    contribution_id="admin.endpoint_contracts",
                ),
            ),
        ))


class _AgentRuntimeModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "runtime.agent",
            domain="agent",
            dependency="admin.api",
            phase=80,
            contribution_kind="port",
            additional_contributions=(
                ModuleContributionRef(
                    kind="observer_hook",
                    contribution_id="runtime.logging",
                ),
                ModuleContributionRef(
                    kind="policy",
                    contribution_id="timing.model_mode",
                ),
                ModuleContributionRef(
                    kind="policy",
                    contribution_id="task.resilience",
                ),
                ModuleContributionRef(
                    kind="content_rule",
                    contribution_id="content.rules",
                ),
            ),
        ))
        self._dependencies = dependencies
        self._application: Any | None = None
        self._session: object | None = None
        self._bridge_started = False

    @staticmethod
    def _state(application: object) -> object:
        state = getattr(application, "state", None)
        if state is None:
            raise RuntimeError("runtime.agent 缺少 application.state")
        return state

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        application = runtime_context.application
        if application is None:
            raise RuntimeError("runtime.agent 缺少 application")
        state = self._state(application)
        self._application = application
        self._session = await self._dependencies.init_new_api_session()
        state.new_api_session = self._session
        state.bridge = None
        try:
            if not runtime_context.testing:
                logger = runtime_context.logger
                if logger is None:
                    raise RuntimeError("runtime.agent 缺少 logger")
                await self._dependencies.run_startup_network_check(
                    logger,
                    self._session,
                )
                state.bridge = await self._dependencies.init_bridge()
                self._bridge_started = True
                self._dependencies.bind_agent_runtime(state.bridge)
        except BaseException:
            if self._bridge_started:
                self._dependencies.clear_agent_runtime_bindings()
                try:
                    await self._dependencies.shutdown_bridge()
                finally:
                    self._bridge_started = False
            state.bridge = None
            state.new_api_session = None
            await self._dependencies.shutdown_new_api_session(
                self._session
            )
            self._session = None
            self._application = None
            raise

    async def _stop(self) -> None:
        errors: list[BaseException] = []
        application = self._application
        state = self._state(application) if application is not None else None
        if self._bridge_started:
            self._dependencies.clear_agent_runtime_bindings()
            try:
                await self._dependencies.shutdown_bridge()
            except BaseException as exc:
                errors.append(exc)
            self._bridge_started = False
        try:
            await self._dependencies.shutdown_new_api_session(
                self._session
            )
        except BaseException as exc:
            errors.append(exc)
        self._session = None
        if state is not None:
            state.bridge = None
            state.new_api_session = None
        self._application = None
        _raise_first(errors)


class _GroupMemoryModule(_BuiltinModule):
    def __init__(self, dependencies: ApplicationModuleDependencies) -> None:
        super().__init__(_manifest(
            "group.memory",
            domain="group",
            dependency="runtime.agent",
            phase=90,
            contribution_kind="task",
        ))
        self._dependencies = dependencies

    async def _start(self, runtime_context: ModuleRuntimeContext) -> None:
        del runtime_context
        self._dependencies.init_legacy_memory()


def build_application_modules(
    dependencies: ApplicationModuleDependencies,
) -> tuple[ApplicationModule, ...]:
    """构建固定的十模块组合；不按 import 顺序隐式发现插件。"""

    return (
        _AgentRuntimeModule(dependencies),
        _ModelProviderModule(dependencies),
        _PromptRuntimeModule(dependencies),
        _ToolRuntimeModule(),
        _MemoryRuntimeModule(dependencies),
        _TelemetryModule(dependencies),
        _DeliveryOutboundModule(dependencies),
        _GroupMemoryModule(dependencies),
        _SandboxControlPlaneModule(dependencies),
        _AdminApiModule(),
    )


__all__ = [
    "ApplicationModuleDependencies",
    "build_application_modules",
]
