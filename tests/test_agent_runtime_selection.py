from __future__ import annotations

import pytest

from core.agent_runtime import (
    AgentRuntimeKind,
    AgentRuntimeSelectionPolicy,
    parse_runtime_scope_ids,
)


def test_runtime_selection_defaults_to_native_and_is_session_stable():
    policy = AgentRuntimeSelectionPolicy()

    first = policy.select(session_id="private_10001", user_id="10001")
    second = policy.select(session_id="private_10001", user_id="other")

    assert first == second
    assert first.kind is AgentRuntimeKind.NATIVE
    assert first.reason == "default"
    assert 0 <= first.bucket < 10_000
    assert len(first.scope_sha256) == 64
    assert len(first.policy_sha256) == 64


def test_runtime_selection_kt_allowlist_precedes_percentage_rollout():
    policy = AgentRuntimeSelectionPolicy(
        kt_enabled=True,
        kt_session_ids=frozenset({"group_42"}),
        kt_percentage_basis_points=1,
    )

    selected = policy.select(session_id="group_42", user_id="10001")

    assert selected.kind is AgentRuntimeKind.KT
    assert selected.reason == "kt_session_allowlist"


def test_runtime_selection_percentage_uses_deterministic_basis_points():
    all_kt = AgentRuntimeSelectionPolicy(
        kt_enabled=True,
        kt_percentage_basis_points=10_000,
    )
    no_kt = AgentRuntimeSelectionPolicy(
        kt_enabled=True,
        kt_percentage_basis_points=0,
    )

    assert all_kt.select(session_id="private_1").kind is AgentRuntimeKind.KT
    assert all_kt.select(session_id="private_1").reason == "kt_percentage_rollout"
    assert no_kt.select(session_id="private_1").kind is AgentRuntimeKind.NATIVE


def test_runtime_selection_policy_hash_covers_effective_configuration():
    first = AgentRuntimeSelectionPolicy(
        kt_enabled=True,
        kt_session_ids=frozenset({"b", "a"}),
        kt_percentage_basis_points=2500,
    )
    same = AgentRuntimeSelectionPolicy(
        kt_enabled=True,
        kt_session_ids=frozenset({"a", "b"}),
        kt_percentage_basis_points=2500,
    )
    changed = AgentRuntimeSelectionPolicy(
        kt_enabled=True,
        kt_session_ids=frozenset({"a", "b"}),
        kt_percentage_basis_points=2501,
    )

    assert first.policy_sha256 == same.policy_sha256
    assert first.policy_sha256 != changed.policy_sha256


@pytest.mark.parametrize(
    "kwargs",
    [
        {"default_kind": "invalid"},
        {"kt_enabled": False, "default_kind": "kt"},
        {"kt_enabled": False, "kt_session_ids": frozenset({"private_1"})},
        {"kt_enabled": False, "kt_percentage_basis_points": 1},
        {"kt_enabled": True, "kt_percentage_basis_points": -1},
        {"kt_enabled": True, "kt_percentage_basis_points": 10_001},
    ],
)
def test_runtime_selection_rejects_inconsistent_configuration(kwargs):
    with pytest.raises(ValueError):
        AgentRuntimeSelectionPolicy(**kwargs)


def test_runtime_selection_requires_identity_and_rejects_wildcard_scope():
    with pytest.raises(ValueError, match="至少需要"):
        AgentRuntimeSelectionPolicy().select(session_id="", user_id="")
    with pytest.raises(ValueError, match="通配符"):
        parse_runtime_scope_ids("private_1,group_*")


def test_bridge_agent_identity_does_not_follow_runtime_implementation_name():
    from nanobot_kt.bridge import NanobotBridge
    from nanobot_kt.bridge_runtime_support import bridge_agent_id

    native = NanobotBridge(
        "creatures/nanobot",
        runtime_kind=AgentRuntimeKind.NATIVE,
    )
    kt = NanobotBridge(
        "creatures/nanobot",
        runtime_kind=AgentRuntimeKind.KT,
    )
    native._runtime_name = "native:temporary"
    kt._runtime_name = "kt:temporary"

    assert bridge_agent_id(native) == bridge_agent_id(kt) == "nanobot"


class _FakeBridge:
    def __init__(
        self,
        runtime_kind: AgentRuntimeKind,
        *,
        fail: bool = False,
        calls: list[AgentRuntimeKind] | None = None,
    ) -> None:
        self.runtime_kind = runtime_kind
        self.fail = fail
        self.calls = calls if calls is not None else []
        self.stop_count = 0

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        self.stop_count += 1

    async def handle_message(self, _query: str, **_kwargs) -> str:
        self.calls.append(self.runtime_kind)
        if self.fail:
            raise RuntimeError("selected runtime failed after side effect")
        return self.runtime_kind.value

    def pop_last_reply_meta(self, _session_id: str = ""):
        return None


@pytest.mark.asyncio
async def test_bridge_pool_selects_native_default_and_kt_gray_scope(monkeypatch):
    from nanobot_kt.bridge import NanobotBridgePool

    events: list[dict] = []
    monkeypatch.setattr(
        "core.runtime.event_bus.emit_runtime_event",
        lambda name, phase, **kwargs: events.append({
            "name": name,
            "phase": phase,
            **kwargs,
        }),
    )
    created: list[AgentRuntimeKind] = []

    def factory(kind: AgentRuntimeKind):
        created.append(kind)
        return _FakeBridge(kind)

    policy = AgentRuntimeSelectionPolicy(
        kt_enabled=True,
        kt_session_ids=frozenset({"group_42"}),
    )
    pool = NanobotBridgePool(
        selection_policy=policy,
        bridge_factory=factory,
    )
    await pool.start()
    try:
        assert await pool.handle_message("a", session_id="private_1") == "native"
        assert await pool.handle_message("b", session_id="group_42") == "kt"
    finally:
        await pool.stop()

    assert created == [AgentRuntimeKind.NATIVE, AgentRuntimeKind.KT]
    selection_events = [
        event for event in events if event["name"] == "agent.runtime_selection"
    ]
    assert [
        event["attributes"]["selected_runtime"] for event in selection_events
    ] == ["native", "kt"]
    assert selection_events[0]["attributes"]["changed"] is False
    assert selection_events[1]["attributes"]["selection_reason"] == (
        "kt_session_allowlist"
    )
    assert "private_1" not in str(selection_events)
    assert "group_42" not in str(selection_events)


@pytest.mark.asyncio
async def test_bridge_pool_never_falls_back_across_runtime_after_failure():
    from nanobot_kt.bridge import NanobotBridgePool

    factory_calls: list[AgentRuntimeKind] = []
    side_effect_calls: list[AgentRuntimeKind] = []

    def factory(kind: AgentRuntimeKind):
        factory_calls.append(kind)
        return _FakeBridge(kind, fail=True, calls=side_effect_calls)

    pool = NanobotBridgePool(
        selection_policy=AgentRuntimeSelectionPolicy(),
        bridge_factory=factory,
    )
    await pool.start()
    try:
        with pytest.raises(RuntimeError, match="side effect"):
            await pool.handle_message("执行有副作用操作", session_id="private_1")
    finally:
        await pool.stop()

    assert factory_calls == [AgentRuntimeKind.NATIVE]
    assert side_effect_calls == [AgentRuntimeKind.NATIVE]


@pytest.mark.asyncio
async def test_bridge_pool_runtime_change_stops_old_bridge_and_emits_change(monkeypatch):
    from nanobot_kt.bridge import NanobotBridgePool

    events: list[dict] = []
    monkeypatch.setattr(
        "core.runtime.event_bus.emit_runtime_event",
        lambda name, phase, **kwargs: events.append({
            "name": name,
            "phase": phase,
            **kwargs,
        }),
    )
    created: list[_FakeBridge] = []

    def factory(kind: AgentRuntimeKind):
        bridge = _FakeBridge(kind)
        created.append(bridge)
        return bridge

    pool = NanobotBridgePool(
        selection_policy=AgentRuntimeSelectionPolicy(),
        bridge_factory=factory,
    )
    await pool.start()
    try:
        assert await pool.handle_message("a", session_id="private_1") == "native"
        # 生产配置为 restart_required；这里替换冻结策略只用于验证 Pool 的
        # 切换边界，不提供运行时热更新入口。
        pool._selection_policy = AgentRuntimeSelectionPolicy(
            default_kind=AgentRuntimeKind.KT,
            kt_enabled=True,
        )
        assert await pool.handle_message("b", session_id="private_1") == "kt"
        assert created[0].stop_count == 1
    finally:
        await pool.stop()

    selection_events = [
        event for event in events if event["name"] == "agent.runtime_selection"
    ]
    assert selection_events[-1]["attributes"]["previous_runtime"] == "native"
    assert selection_events[-1]["attributes"]["selected_runtime"] == "kt"
    assert selection_events[-1]["attributes"]["changed"] is True


@pytest.mark.asyncio
async def test_composition_root_builds_native_default_and_passes_frozen_policy(
    monkeypatch,
):
    import bootstrap.lifespan as lifespan
    import nanobot_kt.bridge as bridge_module
    import core.settings_service as settings_module

    class _Settings:
        values = {
            "agent.runtime.default": "native",
            "agent.runtime.kt_enabled": True,
            "agent.runtime.kt_rollout_basis_points": 1250,
            "agent.runtime.kt_session_allowlist": "group_42,private_7",
        }

        def get_str(self, key: str, default: str = "") -> str:
            return str(self.values.get(key, default))

        def get_bool(self, key: str, default: bool = False) -> bool:
            return bool(self.values.get(key, default))

        def get_int(self, key: str, default: int = 0) -> int:
            return int(self.values.get(key, default))

    captured: list[AgentRuntimeSelectionPolicy] = []

    async def fake_init_bridge(*, selection_policy):
        captured.append(selection_policy)
        return "bridge"

    monkeypatch.setattr(settings_module, "settings", _Settings())
    monkeypatch.setattr(bridge_module, "init_bridge", fake_init_bridge)

    assert await lifespan.init_bridge() == "bridge"
    policy = captured[0]
    assert policy.default_kind is AgentRuntimeKind.NATIVE
    assert policy.kt_enabled is True
    assert policy.kt_percentage_basis_points == 1250
    assert policy.kt_session_ids == frozenset({"group_42", "private_7"})
