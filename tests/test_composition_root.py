from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from core.registry import RegistrySnapshot


def _manifest(
    module_id: str,
    *,
    dependencies: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    contributions=(),
    phase: int = 10,
):
    from core.modules import ModuleManifest

    return ModuleManifest(
        module_id=module_id,
        version="1.0.0",
        owner="tests",
        domain="tests",
        required_modules=dependencies,
        provided_capabilities=provides,
        contributions=tuple(contributions),
        startup_phase=phase,
        shutdown_phase=phase,
        health_checks=("ready",),
        readiness_checks=("ready",),
        release_impacts=("tests",),
    )


@dataclass
class _FakeModule:
    descriptor: object
    calls: list[str]
    fail_start: bool = False
    ready: bool = True

    def manifest(self):
        return self.descriptor

    def register(self, builder):
        for contribution in self.descriptor.contributions:
            builder.register(
                contribution.kind,
                contribution.contribution_id,
            )

    async def start(self, runtime_context):
        self.calls.append(
            f"start:{self.descriptor.module_id}:"
            f"{runtime_context.composition_generation}"
        )
        if self.fail_start:
            raise RuntimeError("secret-start-failure")

    async def stop(self):
        self.calls.append(f"stop:{self.descriptor.module_id}")

    def health(self):
        from core.modules import ModuleHealth, ModuleHealthCheck

        return ModuleHealth(
            status="healthy" if self.ready else "unhealthy",
            ready=self.ready,
            checks=(
                ModuleHealthCheck(
                    name="ready",
                    healthy=self.ready,
                    detail_code="" if self.ready else "not_ready",
                ),
            ),
        )


@pytest.mark.asyncio
async def test_composition_root_uses_shared_registry_and_reverse_stop():
    from core.modules import CompositionRoot, ModuleRuntimeContext

    calls: list[str] = []
    database = _FakeModule(
        _manifest(
            "database.runtime",
            provides=("database.port",),
            phase=10,
        ),
        calls,
    )
    agent = _FakeModule(
        _manifest(
            "runtime.agent",
            dependencies=("database.runtime",),
            provides=("agent.runtime.port",),
            phase=20,
        ),
        calls,
    )
    root = CompositionRoot((agent, database))

    snapshot = root.build()

    assert isinstance(snapshot.modules, RegistrySnapshot)
    assert isinstance(snapshot.contributions, RegistrySnapshot)
    assert snapshot.modules.generation == (
        snapshot.contributions.generation
    )
    assert snapshot.modules.ordered_ids == (
        "database.runtime",
        "runtime.agent",
    )

    await root.start(ModuleRuntimeContext(testing=True))
    assert calls == [
        "start:database.runtime:1",
        "start:runtime.agent:1",
    ]
    assert root.require_module("runtime.agent") is agent

    await root.stop()
    assert calls[-2:] == [
        "stop:runtime.agent",
        "stop:database.runtime",
    ]
    with pytest.raises(RuntimeError, match="未运行"):
        root.require_module("runtime.agent")


def test_composition_root_rejects_duplicate_capability_before_start():
    from core.modules import CompositionRoot, CompositionValidationError

    calls: list[str] = []
    root = CompositionRoot((
        _FakeModule(
            _manifest("module.first", provides=("shared.port",)),
            calls,
        ),
        _FakeModule(
            _manifest("module.second", provides=("shared.port",)),
            calls,
        ),
    ))

    with pytest.raises(
        CompositionValidationError,
        match="shared.port",
    ):
        root.build()
    assert calls == []


def test_composition_root_rejects_dependency_cycle():
    from core.modules import CompositionRoot
    from core.registry.validation import RegistryDependencyError

    root = CompositionRoot((
        _FakeModule(
            _manifest(
                "module.first",
                dependencies=("module.second",),
            ),
            [],
        ),
        _FakeModule(
            _manifest(
                "module.second",
                dependencies=("module.first",),
            ),
            [],
        ),
    ))

    with pytest.raises(RegistryDependencyError):
        root.build()


def test_module_registration_must_match_manifest_exactly():
    from core.modules import (
        CompositionRoot,
        CompositionValidationError,
        ModuleContributionRef,
    )

    class MissingRegistration(_FakeModule):
        def register(self, builder):
            del builder

    manifest = _manifest(
        "module.prompt",
        contributions=(
            ModuleContributionRef(
                kind="prompt",
                contribution_id="chat.system",
            ),
        ),
    )
    root = CompositionRoot((
        MissingRegistration(manifest, []),
    ))

    with pytest.raises(
        CompositionValidationError,
        match="未注册声明贡献",
    ):
        root.build()


@pytest.mark.asyncio
async def test_partial_start_failure_stops_started_modules_in_reverse():
    from core.modules import (
        CompositionRoot,
        CompositionStartError,
        CompositionState,
        ModuleRuntimeContext,
    )

    calls: list[str] = []
    first = _FakeModule(
        _manifest("module.first", phase=10),
        calls,
    )
    second = _FakeModule(
        _manifest(
            "module.second",
            dependencies=("module.first",),
            phase=20,
        ),
        calls,
        fail_start=True,
    )
    third = _FakeModule(
        _manifest(
            "module.third",
            dependencies=("module.second",),
            phase=30,
        ),
        calls,
    )
    root = CompositionRoot((third, second, first))

    with pytest.raises(
        CompositionStartError,
        match="module.second",
    ) as caught:
        await root.start(ModuleRuntimeContext(testing=True))

    assert "secret-start-failure" not in str(caught.value)
    assert calls == [
        "start:module.first:1",
        "start:module.second:1",
        "stop:module.first",
    ]
    assert root.state is CompositionState.FAILED


@pytest.mark.asyncio
async def test_unhealthy_module_fails_start_and_is_stopped():
    from core.modules import (
        CompositionRoot,
        CompositionStartError,
        ModuleRuntimeContext,
    )

    calls: list[str] = []
    module = _FakeModule(
        _manifest("module.unhealthy"),
        calls,
        ready=False,
    )
    root = CompositionRoot((module,))

    with pytest.raises(CompositionStartError, match="module.unhealthy"):
        await root.start(ModuleRuntimeContext(testing=True))

    assert calls == [
        "start:module.unhealthy:1",
        "stop:module.unhealthy",
    ]


@pytest.mark.asyncio
async def test_start_cancellation_cleans_started_modules_and_propagates():
    from core.modules import (
        CompositionRoot,
        CompositionState,
        ModuleRuntimeContext,
    )

    calls: list[str] = []
    first = _FakeModule(
        _manifest("module.first", phase=10),
        calls,
    )

    class CancelledModule(_FakeModule):
        async def start(self, runtime_context):
            del runtime_context
            calls.append("start:module.cancelled:1")
            raise asyncio.CancelledError

    cancelled = CancelledModule(
        _manifest(
            "module.cancelled",
            dependencies=("module.first",),
            phase=20,
        ),
        calls,
    )
    root = CompositionRoot((cancelled, first))

    with pytest.raises(asyncio.CancelledError):
        await root.start(ModuleRuntimeContext(testing=True))

    assert calls == [
        "start:module.first:1",
        "start:module.cancelled:1",
        "stop:module.first",
    ]
    assert root.state is CompositionState.FAILED


def test_kt_boundary_scan_accepts_framework_independent_core(tmp_path):
    from scripts.check_architecture import (
        check_kt_framework_boundaries,
    )

    core_file = tmp_path / "core_service.py"
    core_file.write_text(
        "from core.agent_runtime.gateway import get_agent_gateway\n",
        encoding="utf-8",
    )

    assert check_kt_framework_boundaries((core_file,)) == []


def test_kt_boundary_scan_rejects_static_and_function_local_imports(
    tmp_path,
):
    from scripts.check_architecture import (
        check_kt_framework_boundaries,
    )

    direct = tmp_path / "direct.py"
    direct.write_text(
        "from nanobot_kt.bridge import get_bridge\n",
        encoding="utf-8",
    )
    dynamic = tmp_path / "dynamic.py"
    dynamic.write_text(
        "def load():\n"
        "    from kohakuterrarium.modules.plugin.base import BasePlugin\n",
        encoding="utf-8",
    )

    errors = check_kt_framework_boundaries((direct, dynamic))

    assert len(errors) == 2
    assert "nanobot_kt.bridge" in errors[0]
    assert "kohakuterrarium.modules.plugin.base" in errors[1]


def test_identity_prefix_scan_accepts_typed_identity_usage(tmp_path):
    from scripts.check_architecture import (
        check_identity_prefix_inference_boundaries,
    )

    typed = tmp_path / "typed_identity.py"
    typed.write_text(
        "from foundation.identity import resolve_chat_stream_identity\n"
        "identity = resolve_chat_stream_identity(\n"
        "    platform='qq', chat_type='group', session_id='42'\n"
        ")\n",
        encoding="utf-8",
    )

    assert check_identity_prefix_inference_boundaries((typed,)) == []


def test_identity_prefix_scan_rejects_python_and_sql_prefix_inference(
    tmp_path,
):
    from scripts.check_architecture import (
        check_identity_prefix_inference_boundaries,
    )

    python_rule = tmp_path / "python_rule.py"
    python_rule.write_text(
        "def is_group(value):\n"
        "    return value.startswith('group_')\n",
        encoding="utf-8",
    )
    sqlalchemy_rule = tmp_path / "sqlalchemy_rule.py"
    sqlalchemy_rule.write_text(
        "def groups(ChatLog):\n"
        "    return ChatLog.session_id.like('qq:%:group')\n",
        encoding="utf-8",
    )
    raw_sql_rule = tmp_path / "raw_sql_rule.py"
    raw_sql_rule.write_text(
        "QUERY = \"SELECT 1 WHERE session_id LIKE 'group_%'\"\n",
        encoding="utf-8",
    )

    errors = check_identity_prefix_inference_boundaries(
        (python_rule, sqlalchemy_rule, raw_sql_rule)
    )

    assert len(errors) == 3
    assert "startswith('group_')" in errors[0]
    assert "like('qq:%:group')" in errors[1]
    assert "SQL LIKE 群聊前缀" in errors[2]


def test_message_contract_boundary_accepts_typed_bridge_and_no_registry(
    tmp_path,
):
    from scripts.check_architecture import (
        check_message_contract_boundaries,
    )

    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "from nanobot_kt.message_adapter import MessageContractBridgeMixin\n"
        "class NanobotBridge(MessageContractBridgeMixin):\n"
        "    pass\n"
        "class NanobotBridgePool(MessageContractBridgeMixin):\n"
        "    pass\n",
        encoding="utf-8",
    )
    production = tmp_path / "transport.py"
    production.write_text(
        "def render_message(contract):\n"
        "    return contract\n",
        encoding="utf-8",
    )

    assert check_message_contract_boundaries(
        paths=(production,),
        bridge_path=bridge,
    ) == []


def test_message_contract_boundary_rejects_dynamic_channel_registry_and_untyped_bridge(
    tmp_path,
):
    from scripts.check_architecture import (
        check_message_contract_boundaries,
    )

    bridge = tmp_path / "bridge.py"
    bridge.write_text(
        "class NanobotBridge:\n"
        "    pass\n"
        "class NanobotBridgePool:\n"
        "    pass\n",
        encoding="utf-8",
    )
    production = tmp_path / "dynamic_channel.py"
    production.write_text(
        "class ChannelRegistry:\n"
        "    pass\n",
        encoding="utf-8",
    )

    errors = check_message_contract_boundaries(
        paths=(production,),
        bridge_path=bridge,
    )

    assert len(errors) == 3
    assert "ChannelRegistry" in errors[0]
    assert "NanobotBridge" in errors[1]
    assert "NanobotBridgePool" in errors[2]
