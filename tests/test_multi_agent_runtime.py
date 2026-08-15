from __future__ import annotations

from pathlib import Path

import pytest


class _MessageGateway:
    async def handle_message(self, content: str, **_kwargs):
        return content


class _ManagedGateway(_MessageGateway):
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def _registration(
    agent_id: str,
    *,
    default: bool = False,
    entrypoints: tuple[str, ...] = ("chat",),
):
    from core.agent_runtime.registry import (
        AgentRuntimeDescriptor,
        AgentRuntimeRegistration,
    )

    shared = _MessageGateway()
    return AgentRuntimeRegistration(
        descriptor=AgentRuntimeDescriptor(
            agent_id=agent_id,
            display_name=agent_id.upper(),
            description=f"{agent_id} 测试 Agent",
            adapter="test",
            source_ref=f"creatures/{agent_id}",
            source_sha256="a" * 64,
            runtime_policy_sha256="b" * 64,
            allowed_entrypoints=entrypoints,
            default=default,
        ),
        gateway_provider=lambda: shared,
        isolated_gateway_factory=_ManagedGateway,
    )


def test_agent_runtime_registry_freezes_multiple_agents_and_routes_by_id():
    from core.agent_runtime import (
        AgentRuntimeCapabilityError,
        AgentRuntimeNotFoundError,
    )
    from core.agent_runtime.registry import AgentRuntimeRegistry

    registry = AgentRuntimeRegistry.build((
        _registration(
            "nanobot",
            default=True,
            entrypoints=("chat", "agent_link", "scheduled", "research"),
        ),
        _registration("pabot", entrypoints=("chat", "agent_link")),
    ))

    assert registry.default_agent_id == "nanobot"
    assert registry.snapshot.ordered_ids == ("nanobot", "pabot")
    assert registry.require_registration("pabot", entrypoint="chat").descriptor.agent_id == (
        "pabot"
    )
    assert registry.require_registration("", entrypoint="chat").descriptor.agent_id == (
        "nanobot"
    )
    with pytest.raises(AgentRuntimeNotFoundError):
        registry.require_registration("missing", entrypoint="chat")
    with pytest.raises(AgentRuntimeCapabilityError, match="入口"):
        registry.require_registration("pabot", entrypoint="research")


def test_agent_runtime_registry_requires_exactly_one_default():
    from core.agent_runtime.registry import AgentRuntimeRegistry

    with pytest.raises(ValueError, match="默认"):
        AgentRuntimeRegistry.build((_registration("nanobot"),))
    with pytest.raises(ValueError, match="默认"):
        AgentRuntimeRegistry.build((
            _registration("nanobot", default=True),
            _registration("pabot", default=True),
        ))


def test_gateway_binding_routes_registered_agent_and_preserves_default_compatibility():
    from core.agent_runtime.gateway import (
        bind_agent_runtime_registry,
        clear_agent_runtime_bindings,
        get_agent_gateway,
        list_registered_agents,
    )
    from core.agent_runtime.registry import AgentRuntimeRegistry

    registry = AgentRuntimeRegistry.build((
        _registration("nanobot", default=True),
        _registration("pabot"),
    ))
    clear_agent_runtime_bindings()
    try:
        bind_agent_runtime_registry(registry)

        assert get_agent_gateway() is registry.require_registration(
            "nanobot",
            entrypoint="chat",
        ).gateway_provider()
        assert get_agent_gateway("pabot") is registry.require_registration(
            "pabot",
            entrypoint="chat",
        ).gateway_provider()
        assert [item.agent_id for item in list_registered_agents()] == [
            "nanobot",
            "pabot",
        ]
    finally:
        clear_agent_runtime_bindings()


def test_creature_catalog_loads_explicit_pabot_profile_and_tool_policy():
    from nanobot_kt.agent_catalog import load_creature_agent_spec

    creatures_root = Path(__file__).resolve().parents[1] / "creatures"
    spec = load_creature_agent_spec("pabot", creatures_root=creatures_root)

    assert spec.agent_id == "pabot"
    assert spec.display_name == "PAbot"
    assert "研究" in spec.profile
    assert "asset_import" in spec.allowed_tool_names
    assert "asset_publish" in spec.allowed_tool_names
    assert "agent_link" in spec.allowed_entrypoints
    assert len(spec.source_sha256) == 64


def test_pabot_tool_policy_rejects_unlisted_tools_but_keeps_agent_link_tools():
    from core.tool_plan import (
        ToolPlan,
        additional_tool_schemas_scope,
        extend_tool_plan,
    )
    from nanobot_kt.bridge import NanobotBridge

    def schema(name: str) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }

    base = ToolPlan.from_effective_tools(
        enabled={"reply": True, "persona_update": True},
        tool_schemas=[schema("reply"), schema("persona_update")],
        chat_type="private",
    )
    dynamic_schema = schema("meapet.echo")
    bridge = NanobotBridge(
        "creatures/pabot",
        agent_id="pabot",
        allowed_tool_names=frozenset({"reply"}),
        allow_dynamic_tools=True,
    )

    with additional_tool_schemas_scope((dynamic_schema,)):
        extended = extend_tool_plan(
            base,
            (dynamic_schema,),
            chat_type="private",
            platform="meapet",
            session_id="private_test",
        )
        restricted = bridge._restrict_agent_tool_plan(extended)

    assert restricted.can_execute("reply") is True
    assert restricted.can_execute("meapet.echo") is True
    assert restricted.can_execute("persona_update") is False
    assert "冻结工具策略未授权" in restricted.disabled_reason("persona_update")


@pytest.mark.asyncio
async def test_pabot_profile_enters_trusted_identity_prompt():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    plan = await compile_prompt_plan(PromptCompileRequest(
        chat_type="private",
        platform="qq",
        session_id="private_pabot",
        user_id="user-pabot",
        sender_id="user-pabot",
        user_input="研究这个问题",
        agent_id="pabot",
        agent_profile="专业研究与受控操作",
    ))
    identity_messages = [
        message["content"]
        for message in plan.messages
        if isinstance(message.get("content"), str)
        and "<identity_context>" in message["content"]
    ]

    assert len(identity_messages) == 1
    assert "当前 Agent：pabot" in identity_messages[0]
    assert "职责：专业研究与受控操作" in identity_messages[0]


@pytest.mark.asyncio
async def test_multi_agent_bridge_manager_owns_isolated_pools():
    from core.agent_runtime import AgentRuntimeSelectionPolicy
    from nanobot_kt.agent_catalog import load_creature_agent_specs
    from nanobot_kt.multi_agent_runtime import NanobotAgentRuntimeManager

    class _Pool(_ManagedGateway):
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False

        async def start(self) -> None:
            self.started = True

        async def stop(self) -> None:
            self.started = False

        def create_isolated_bridge(self):
            return _ManagedGateway()

    specs = load_creature_agent_specs(("nanobot", "pabot"))
    manager = NanobotAgentRuntimeManager(
        specs,
        selection_policy=AgentRuntimeSelectionPolicy(),
        pool_factory=_Pool,
    )

    await manager.start()
    try:
        nanobot = manager.get_pool("nanobot")
        pabot = manager.get_pool("pabot")
        assert nanobot is not pabot
        assert nanobot.started is True
        assert pabot.started is True
        assert pabot.kwargs["agent_id"] == "pabot"
        assert "asset_publish" in pabot.kwargs["allowed_tool_names"]
        registry = manager.build_runtime_registry()
        assert registry.default_agent_id == "nanobot"
        assert registry.require_registration(
            "pabot",
            entrypoint="agent_link",
        ).gateway_provider() is pabot
        assert registry.require_registration(
            "pabot",
            entrypoint="a2a",
        ).gateway_provider() is pabot
    finally:
        await manager.stop()

    assert nanobot.started is False
    assert pabot.started is False


@pytest.mark.asyncio
async def test_pool_message_adapter_uses_registered_agent_identity(monkeypatch):
    from api.chat_request_contract import (
        ChatProxyRequest,
        build_chat_message_contract,
        normalize_request_client_meta,
    )
    from nanobot_kt import message_adapter

    captured: dict[str, str] = {}
    original = message_adapter.build_kt_message_invocation

    def _capture(*args, **kwargs):
        captured["agent_id"] = kwargs["agent_id"]
        return original(*args, **kwargs)

    class _PabotPool(message_adapter.MessageContractBridgeMixin):
        agent_id = "pabot"

        async def handle_message(self, content: str, **_kwargs):
            return content

    monkeypatch.setattr(message_adapter, "build_kt_message_invocation", _capture)
    request = ChatProxyRequest(
        user_id="multi-agent-user",
        session_id="private_multi-agent-user",
        query="研究任务",
        client_meta={"platform": "web"},
    )
    normalize_request_client_meta(request, expected_chat_type="private")

    result = await _PabotPool().handle_message_contract(
        build_chat_message_contract(request),
        content="研究任务",
        runtime_user_id="multi-agent-user",
        runtime_session_id="private_multi-agent-user",
        sender_name="用户",
    )

    assert result == "研究任务"
    assert captured["agent_id"] == "pabot"


@pytest.mark.asyncio
async def test_agent_link_can_route_pabot_on_native_runtime_without_dynamic_tools():
    from core.agent_link.runtime import (
        AgentLinkChatRequest,
        AgentLinkClientIdentity,
        AgentLinkSessionKey,
    )
    from nanobot_kt.agent_link_adapter import KtAgentLinkChatAdapter

    captured: dict[str, object] = {}

    class _NativeBridge:
        agent = None

        async def handle_message_contract(self, message, **kwargs):
            captured["message"] = message
            captured["metadata"] = kwargs["metadata"]
            return "PAbot 已完成"

    class _Pool:
        def _session_key(self, *, user_id, session_id):
            return session_id or user_id

        async def _acquire_bridge(self, _key):
            return _NativeBridge()

        async def _release_bridge(self, key):
            captured["released_key"] = key

    class _NoToolCaller:
        async def call_tool(self, *_args, **_kwargs):
            raise AssertionError("无动态工具时不应调用前端工具")

    resolved: list[str] = []
    request = AgentLinkChatRequest(
        key=AgentLinkSessionKey("pabot-user", "pabot-session"),
        request_id="pabot-turn",
        content="执行研究任务",
        user_text="执行研究任务",
        history=(),
        frontend_context={},
        files=(),
        tools=(),
        target_agent_id="pabot",
        client=AgentLinkClientIdentity(
            platform_id="a2a-client",
            name="A2A Client",
            version="1.0",
        ),
        policy_profile="external_private",
    )

    answer = await KtAgentLinkChatAdapter(
        bridge_pool_resolver=lambda agent_id: (
            resolved.append(agent_id) or _Pool()
        ),
    ).run_chat(request, _NoToolCaller())

    assert answer == "PAbot 已完成"
    assert resolved == ["pabot"]
    assert captured["message"].gateway.source == "agent_link"
    assert captured["metadata"]["agent_id"] == "pabot"
    assert captured["released_key"] == request.key.bridge_session_id


def test_session_agent_defaults_to_nanobot_and_round_trips_admin_api(
    client,
    db_session,
    monkeypatch,
):
    from app.session_config import resolve_session_agent_id
    from core.agent_runtime.gateway import (
        bind_agent_runtime_registry,
        clear_agent_runtime_bindings,
    )
    from core.agent_runtime.registry import AgentRuntimeRegistry
    from core.database import ChatStreamConfig

    registry = AgentRuntimeRegistry.build((
        _registration("nanobot", default=True),
        _registration("pabot"),
    ))
    clear_agent_runtime_bindings()
    bind_agent_runtime_registry(registry)
    monkeypatch.setattr(
        "api.admin_routes.NANOBOT_ADMIN_TOKEN",
        "multi-agent-token",
    )
    headers = {"Authorization": "Bearer multi-agent-token"}
    try:
        assert resolve_session_agent_id(
            db_session,
            platform="qq",
            chat_type="private",
            session_id="agent-session",
        ) == "nanobot"

        registry_response = client.get(
            "/api/v1/admin/agent-runtimes",
            headers=headers,
        )
        update_response = client.put(
            "/api/v1/admin/configs/qq:agent-session:private",
            json={"agent_id": "pabot"},
            headers=headers,
        )
        invalid_response = client.put(
            "/api/v1/admin/configs/qq:agent-session:private",
            json={"agent_id": "missing"},
            headers=headers,
        )

        assert registry_response.status_code == 200
        assert registry_response.json()["default_agent_id"] == "nanobot"
        assert [
            item["agent_id"] for item in registry_response.json()["items"]
        ] == ["nanobot", "pabot"]
        assert update_response.status_code == 200, update_response.text
        assert update_response.json()["agent_id"] == "pabot"
        assert invalid_response.status_code == 422
        db_session.expire_all()
        assert db_session.get(
            ChatStreamConfig,
            "qq:agent-session:private",
        ).agent_id == "pabot"
        assert resolve_session_agent_id(
            db_session,
            platform="qq",
            chat_type="private",
            session_id="agent-session",
        ) == "pabot"
    finally:
        clear_agent_runtime_bindings()


def test_chat_stream_agent_id_migration_is_idempotent():
    from sqlalchemy import create_engine, inspect, text

    from core.schema_migrations import _chat_stream_agent_id_column

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE chat_stream_configs "
            "(chat_stream_id VARCHAR PRIMARY KEY)"
        ))
        connection.execute(text(
            "INSERT INTO chat_stream_configs(chat_stream_id) "
            "VALUES ('qq:migration:private')"
        ))
        _chat_stream_agent_id_column(connection, engine, None)
        _chat_stream_agent_id_column(connection, engine, None)
        stored = connection.execute(text(
            "SELECT agent_id FROM chat_stream_configs"
        )).scalar_one()

    assert "agent_id" in {
        item["name"] for item in inspect(engine).get_columns("chat_stream_configs")
    }
    assert stored == "nanobot"


def test_session_config_webui_exposes_registered_agent_selector():
    source = Path(
        "webui/src/features/session-config/SessionConfigsPage.jsx"
    ).read_text(encoding="utf-8")

    assert "api.get('/agent-runtimes')" in source
    assert 'id="session-config-agent"' in source
    assert "agent_id: form.agent_id" in source
