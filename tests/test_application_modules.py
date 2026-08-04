from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest


@dataclass
class _FakeCallbacks:
    calls: list[str] = field(default_factory=list)
    fail_at: str = ""
    maintenance: object = field(default_factory=object)
    retrieval: object = field(default_factory=object)
    sandbox_runner: object = field(default_factory=object)
    telemetry_runtime: object = field(default_factory=object)
    schedulers: object = field(default_factory=object)
    session: object = field(default_factory=object)
    bridge: object = field(default_factory=object)

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self.fail_at == name:
            raise RuntimeError(f"failure:{name}")

    def init_db(self) -> None:
        self._record("init_db")

    def start_sqlite_maintenance(self) -> object:
        self._record("start_sqlite")
        return self.maintenance

    def stop_sqlite_maintenance(self, worker: object | None) -> None:
        assert worker is self.maintenance
        self._record("stop_sqlite")

    def start_retrieval_runtime(self) -> object:
        self._record("start_retrieval")
        return self.retrieval

    def stop_retrieval_runtime(self, executor: object | None) -> None:
        assert executor is self.retrieval
        self._record("stop_retrieval")

    def start_proactive_runtime(self) -> None:
        self._record("start_proactive")

    def stop_proactive_runtime(self) -> None:
        self._record("stop_proactive")

    def start_telemetry_runtime(self) -> object:
        self._record("start_telemetry")
        return self.telemetry_runtime

    def stop_telemetry_runtime(self, runtime: object | None) -> None:
        assert runtime is self.telemetry_runtime
        self._record("stop_telemetry")

    def start_sandbox_admin_operations(self, testing: bool) -> object:
        self._record(f"start_sandbox:{testing}")
        return self.sandbox_runner

    def stop_sandbox_admin_operations(self, runner: object | None) -> None:
        assert runner is self.sandbox_runner
        self._record("stop_sandbox")

    def validate_sandbox_asset_token_config(self) -> None:
        self._record("validate_sandbox")

    def run_provider_migration(self) -> None:
        self._record("provider_migration")

    def start_model_runtime(self) -> None:
        self._record("start_model")

    def stop_model_runtime(self) -> None:
        self._record("stop_model")

    def init_prompt_runtimes(self, logger: object) -> None:
        assert logger is not None
        self._record("init_prompt")

    def mark_prompt_runtime_ready(self) -> None:
        self._record("prompt_ready")

    def start_schedulers(self, testing: bool, logger: object) -> object:
        assert logger is not None
        self._record(f"start_schedulers:{testing}")
        return self.schedulers

    def stop_schedulers(self, handles: object | None) -> None:
        assert handles is self.schedulers
        self._record("stop_schedulers")

    async def init_new_api_session(self) -> object:
        self._record("init_session")
        return self.session

    async def shutdown_new_api_session(self, session: object | None) -> None:
        assert session is self.session
        self._record("stop_session")

    async def run_startup_network_check(
        self,
        logger: object,
        session: object,
    ) -> None:
        assert logger is not None
        assert session is self.session
        self._record("network_check")

    async def init_bridge(self) -> object:
        self._record("init_bridge")
        return self.bridge

    async def shutdown_bridge(self) -> None:
        self._record("stop_bridge")

    def bind_agent_runtime(self, bridge: object) -> None:
        assert bridge is self.bridge
        self._record("bind_agent_runtime")

    def clear_agent_runtime_bindings(self) -> None:
        self._record("clear_agent_runtime")

    def init_legacy_memory(self) -> None:
        self._record("init_legacy_memory")

    async def close_push_session(self) -> None:
        self._record("close_push_session")


def _dependencies(fake: _FakeCallbacks):
    from bootstrap.application_modules import ApplicationModuleDependencies

    return ApplicationModuleDependencies(
        init_db=fake.init_db,
        start_sqlite_maintenance=fake.start_sqlite_maintenance,
        stop_sqlite_maintenance=fake.stop_sqlite_maintenance,
        start_retrieval_runtime=fake.start_retrieval_runtime,
        stop_retrieval_runtime=fake.stop_retrieval_runtime,
        start_proactive_runtime=fake.start_proactive_runtime,
        stop_proactive_runtime=fake.stop_proactive_runtime,
        start_telemetry_runtime=fake.start_telemetry_runtime,
        stop_telemetry_runtime=fake.stop_telemetry_runtime,
        start_sandbox_admin_operations=fake.start_sandbox_admin_operations,
        stop_sandbox_admin_operations=fake.stop_sandbox_admin_operations,
        validate_sandbox_asset_token_config=(
            fake.validate_sandbox_asset_token_config
        ),
        run_provider_migration=fake.run_provider_migration,
        start_model_runtime=fake.start_model_runtime,
        stop_model_runtime=fake.stop_model_runtime,
        init_prompt_runtimes=fake.init_prompt_runtimes,
        mark_prompt_runtime_ready=fake.mark_prompt_runtime_ready,
        start_schedulers=fake.start_schedulers,
        stop_schedulers=fake.stop_schedulers,
        init_new_api_session=fake.init_new_api_session,
        shutdown_new_api_session=fake.shutdown_new_api_session,
        run_startup_network_check=fake.run_startup_network_check,
        init_bridge=fake.init_bridge,
        shutdown_bridge=fake.shutdown_bridge,
        bind_agent_runtime=fake.bind_agent_runtime,
        clear_agent_runtime_bindings=fake.clear_agent_runtime_bindings,
        init_legacy_memory=fake.init_legacy_memory,
        close_push_session=fake.close_push_session,
    )


def test_builtin_modules_have_explicit_stable_manifests():
    from bootstrap.application_modules import build_application_modules
    from core.modules import CompositionRoot

    modules = build_application_modules(_dependencies(_FakeCallbacks()))
    snapshot = CompositionRoot(modules).build()

    assert set(snapshot.modules.ordered_ids) == {
        "runtime.agent",
        "runtime.telemetry",
        "model.provider",
        "prompt.runtime",
        "tool.runtime",
        "memory.runtime",
        "delivery.outbound",
        "group.memory",
        "sandbox.control_plane",
        "admin.api",
    }
    assert snapshot.modules.ordered_ids == (
        "memory.runtime",
        "runtime.telemetry",
        "sandbox.control_plane",
        "model.provider",
        "prompt.runtime",
        "tool.runtime",
        "delivery.outbound",
        "admin.api",
        "runtime.agent",
        "group.memory",
    )
    assert len(snapshot.contributions.ordered_ids) == 19
    assert {
        (
            contribution.module_id,
            contribution.kind,
            contribution.contribution_id,
        )
        for contribution in snapshot.contributions
    }.issuperset({
        (
            "runtime.agent",
            "observer_hook",
            "runtime.logging",
        ),
        (
            "runtime.agent",
            "policy",
            "timing.model_mode",
        ),
        (
            "runtime.agent",
            "policy",
            "task.resilience",
        ),
        (
            "prompt.runtime",
            "transform_hook",
            "prompt.contribution",
        ),
        (
            "sandbox.control_plane",
            "policy",
            "sandbox.access",
        ),
        (
            "runtime.agent",
            "content_rule",
            "content.rules",
        ),
        (
            "memory.runtime",
            "job",
            "runtime.job_kernel",
        ),
        (
            "runtime.telemetry",
            "telemetry",
            "runtime.telemetry",
        ),
        (
            "runtime.telemetry",
            "metric",
            "runtime.telemetry_metrics",
        ),
        (
            "admin.api",
            "endpoint",
            "admin.endpoint_contracts",
        ),
    })
    for manifest in snapshot.modules:
        assert manifest.owner
        assert manifest.domain
        assert manifest.provided_capabilities
        assert manifest.contributions
        assert manifest.health_checks == ("lifecycle",)
        assert manifest.readiness_checks == ("lifecycle",)


@pytest.mark.asyncio
async def test_builtin_modules_start_and_stop_owned_resources():
    from bootstrap.application_modules import build_application_modules
    from core.modules import CompositionRoot, ModuleRuntimeContext

    fake = _FakeCallbacks()
    app = SimpleNamespace(state=SimpleNamespace())
    root = CompositionRoot(build_application_modules(_dependencies(fake)))

    await root.start(
        ModuleRuntimeContext(
            application=app,
            testing=False,
            logger=object(),
        )
    )

    assert app.state.new_api_session is fake.session
    assert app.state.bridge is fake.bridge
    assert app.state.job_lease_adapters.job_types() == (
        "agent_run",
        "group_memory_learning",
        "session_summary",
        "inbound_chat",
        "memory_digest",
        "scheduled_workflow",
        "semantic_index",
        "outbound_generation",
        "sandbox_admin_operation",
        "outbound_delivery",
    )
    assert app.state.telemetry_runtime is fake.telemetry_runtime
    assert fake.calls == [
        "init_db",
        "start_sqlite",
        "start_retrieval",
        "start_proactive",
        "start_telemetry",
        "start_sandbox:False",
        "validate_sandbox",
        "provider_migration",
        "start_model",
        "init_prompt",
        "prompt_ready",
        "start_schedulers:False",
        "init_session",
        "network_check",
        "init_bridge",
        "bind_agent_runtime",
        "init_legacy_memory",
    ]

    await root.stop()

    assert fake.calls[-11:] == [
        "clear_agent_runtime",
        "stop_bridge",
        "stop_session",
        "stop_schedulers",
        "close_push_session",
        "stop_model",
        "stop_sandbox",
        "stop_telemetry",
        "stop_proactive",
        "stop_retrieval",
        "stop_sqlite",
    ]
    assert app.state.bridge is None
    assert app.state.new_api_session is None
    assert app.state.job_lease_adapters is None
    assert app.state.telemetry_runtime is None


@pytest.mark.asyncio
async def test_testing_mode_skips_network_and_bridge_but_keeps_other_modules():
    from bootstrap.application_modules import build_application_modules
    from core.modules import CompositionRoot, ModuleRuntimeContext

    fake = _FakeCallbacks()
    app = SimpleNamespace(state=SimpleNamespace())
    root = CompositionRoot(build_application_modules(_dependencies(fake)))

    await root.start(
        ModuleRuntimeContext(
            application=app,
            testing=True,
            logger=object(),
        )
    )
    await root.stop()

    assert "network_check" not in fake.calls
    assert "init_bridge" not in fake.calls
    assert "stop_bridge" not in fake.calls
    assert "start_schedulers:True" in fake.calls
    assert app.state.bridge is None
    assert app.state.new_api_session is None


@pytest.mark.asyncio
async def test_memory_module_rolls_back_internal_partial_start():
    from bootstrap.application_modules import build_application_modules
    from core.modules import (
        CompositionRoot,
        CompositionStartError,
        ModuleRuntimeContext,
    )

    fake = _FakeCallbacks(fail_at="start_proactive")
    root = CompositionRoot(build_application_modules(_dependencies(fake)))

    with pytest.raises(
        CompositionStartError,
        match="memory.runtime",
    ):
        await root.start(
            ModuleRuntimeContext(
                application=SimpleNamespace(state=SimpleNamespace()),
                testing=False,
                logger=object(),
            )
        )

    assert fake.calls == [
        "init_db",
        "start_sqlite",
        "start_retrieval",
        "start_proactive",
        "stop_retrieval",
        "stop_sqlite",
    ]


@pytest.mark.asyncio
async def test_agent_module_rolls_back_session_when_network_check_fails():
    from bootstrap.application_modules import build_application_modules
    from core.modules import (
        CompositionRoot,
        CompositionStartError,
        ModuleRuntimeContext,
    )

    fake = _FakeCallbacks(fail_at="network_check")
    app = SimpleNamespace(state=SimpleNamespace())
    root = CompositionRoot(build_application_modules(_dependencies(fake)))

    with pytest.raises(
        CompositionStartError,
        match="runtime.agent",
    ):
        await root.start(
            ModuleRuntimeContext(
                application=app,
                testing=False,
                logger=object(),
            )
        )

    assert "init_bridge" not in fake.calls
    assert "stop_session" in fake.calls
    assert app.state.bridge is None
    assert app.state.new_api_session is None
