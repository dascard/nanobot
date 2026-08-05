from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_chat_adapter_builds_canonical_private_contract():
    from api.chat_request_contract import (
        ChatProxyRequest,
        build_chat_message_contract,
        normalize_request_client_meta,
    )

    req = ChatProxyRequest(
        user_id="u-1",
        session_id="private_u-1",
        query="你好",
        files=["asset://sha256/abc"],
        message_id="m-1",
        sender_name="小明",
        session_name="私聊",
        client_meta={
            "platform": "web",
            "trace": {
                "request_id": "req-1",
                "correlation_id": "corr-1",
                "source": "web-gateway",
            },
        },
    )
    normalize_request_client_meta(req, expected_chat_type="private")

    message = build_chat_message_contract(req)

    assert message.chat_stream.chat_stream_id == "web:u-1:private"
    assert message.actor.canonical_id == "web:actor:u-1"
    assert message.principal.canonical_id == "web:user:u-1"
    assert message.recipient.canonical_id == "web:user:u-1"
    assert message.trace.request_id == "req-1"
    assert message.trace.correlation_id == "corr-1"
    assert message.gateway.source == "web-gateway"
    assert message.attachments[0].ref == "asset://sha256/abc"


def test_group_adapter_rejects_unknown_onebot_segment():
    from app.group_ingress.message_adapter import (
        build_group_message_contract,
    )
    from foundation.message_contract import MessageContractError

    req = SimpleNamespace(
        group_id="42",
        sender_id="u-1",
        sender_name="小明",
        message="",
        message_id="m-1",
        session_name="测试群",
        files=None,
        client_meta={"platform": "qq", "chat_type": "group"},
        segments=[{"type": "future_magic", "data": {}}],
        mentions=[],
        reply_to=None,
        reply_to_message_id=None,
        reply_to_sender_id=None,
        reply_to_sender_name=None,
        reply_to_content=None,
        bot_id="bot-1",
        self_id="bot-1",
        bot_name="Nanobot",
        sender_is_bot=False,
    )

    with pytest.raises(MessageContractError) as exc:
        build_group_message_contract(req)

    assert exc.value.code == "unsupported_content_part"


def test_group_adapter_extracts_mentions_reply_and_images():
    from app.group_ingress.message_adapter import (
        build_group_message_contract,
    )

    req = SimpleNamespace(
        group_id="42",
        sender_id="u-1",
        sender_name="小明",
        message="你好",
        message_id="m-1",
        session_name="测试群",
        files=["https://example.test/fallback.png"],
        client_meta={
            "platform": "qq",
            "chat_type": "group",
            "trace": {"request_id": "req-group-1"},
        },
        segments=[
            {"type": "text", "data": {"text": "你好"}},
            {"type": "at", "data": {"qq": "bot-1"}},
            {
                "type": "image",
                "data": {"url": "https://example.test/a.png"},
            },
            {"type": "reply", "data": {"id": "source-1"}},
        ],
        mentions=[],
        reply_to={
            "message_id": "source-1",
            "sender_id": "u-2",
            "sender_name": "小红",
            "content": "上一条",
            "is_bot": False,
        },
        reply_to_message_id=None,
        reply_to_sender_id=None,
        reply_to_sender_name=None,
        reply_to_content=None,
        bot_id="bot-1",
        self_id="bot-1",
        bot_name="Nanobot",
        sender_is_bot=False,
        is_at_bot=True,
        is_reply_to_bot=False,
    )

    message = build_group_message_contract(req)

    assert message.chat_stream.chat_stream_id == "qq:42:group"
    assert message.principal.canonical_id == "qq:group:42"
    assert message.mentions[0].actor.actor_id == "bot-1"
    assert message.mentions[0].is_bot is True
    assert message.reply_to is not None
    assert message.reply_to.message_id == "source-1"
    assert any(
        getattr(part, "ref", "") == "https://example.test/a.png"
        for part in message.parts
    )


def test_kt_adapter_identity_fields_override_untrusted_metadata():
    from foundation.identity import (
        ActorIdentity,
        Principal,
        RecipientIdentity,
        resolve_chat_stream_identity,
    )
    from foundation.message_contract import (
        InboundMessageContract,
        TextContent,
    )
    from nanobot_kt.message_adapter import build_kt_message_invocation

    message = InboundMessageContract(
        message_id="m-1",
        chat_stream=resolve_chat_stream_identity(
            platform="qq",
            chat_type="group",
            session_id="group_42",
        ),
        actor=ActorIdentity(platform="qq", actor_id="u-1"),
        recipient=RecipientIdentity(
            platform="qq",
            recipient_type="group",
            recipient_id="42",
        ),
        principal=Principal(
            platform="qq",
            owner_type="group",
            owner_id="42",
        ),
        text="你好",
        parts=(TextContent("你好"),),
    )

    invocation = build_kt_message_invocation(
        message,
        content="<user_input>\n你好\n</user_input>",
        runtime_user_id="group_42",
        runtime_session_id="group_42",
        sender_name="小明",
        metadata={
            "platform": "evil",
            "chat_type": "private",
            "group_id": "other",
            "message_id": "other-message",
        },
        stream=True,
    )

    assert invocation.metadata["platform"] == "qq"
    assert invocation.metadata["chat_type"] == "group"
    assert invocation.metadata["group_id"] == "42"
    assert invocation.metadata["message_id"] == "m-1"
    assert invocation.metadata["chat_stream_id"] == "qq:42:group"
    assert invocation.metadata["principal_id"] == "qq:group:42"
    assert invocation.metadata["principal_owner_type"] == "group"
    assert invocation.metadata["principal_owner_id"] == "42"
    assert invocation.metadata["gateway_transport"] == "qq"
    assert len(invocation.metadata["gateway_binding_id"]) == 64
    assert invocation.stream is True
    assert (
        invocation.runtime_request.context.session_id
        == "qq:42:group"
    )
    assert (
        invocation.runtime_request.context.principal.canonical_id
        == "qq:group:42"
    )
    assert invocation.runtime_request.context.request_id == "m-1"
    assert invocation.runtime_request.context.turn_id == "m-1"
    assert invocation.runtime_request.context.correlation_id == "m-1"
    assert invocation.runtime_request.context.actor.actor_type.value == "user"
    assert invocation.runtime_request.context.actor.actor_id == "u-1"
    assert invocation.runtime_request.content == (
        "<user_input>\n你好\n</user_input>"
    )
    assert invocation.runtime_request.stream is True


def test_kt_adapter_uses_trace_request_id_before_message_id():
    from foundation.identity import (
        ActorIdentity,
        Principal,
        RecipientIdentity,
        resolve_chat_stream_identity,
    )
    from foundation.message_contract import (
        InboundMessageContract,
        MessageTrace,
        TextContent,
    )
    from nanobot_kt.message_adapter import build_kt_message_invocation

    message = InboundMessageContract(
        message_id="m-lower-priority",
        chat_stream=resolve_chat_stream_identity(
            platform="web",
            chat_type="private",
            session_id="u-1",
        ),
        actor=ActorIdentity(platform="web", actor_id="u-1"),
        recipient=RecipientIdentity(
            platform="web",
            recipient_type="user",
            recipient_id="u-1",
        ),
        principal=Principal(
            platform="web",
            owner_type="user",
            owner_id="u-1",
        ),
        text="你好",
        parts=(TextContent("你好"),),
        trace=MessageTrace(
            request_id="req-authoritative",
            trace_id="trace-1",
        ),
    )

    invocation = build_kt_message_invocation(
        message,
        content="<user_input>你好</user_input>",
        runtime_user_id="u-1",
        runtime_session_id="private_u-1",
        sender_name="用户",
    )

    assert (
        invocation.runtime_request.context.request_id
        == "req-authoritative"
    )
    assert invocation.runtime_request.context.trace_id == "trace-1"
    assert invocation.runtime_request.context.turn_id == "m-lower-priority"
    assert invocation.runtime_request.context.correlation_id == "trace-1"
    assert invocation.runtime_request.context.message_id == (
        "m-lower-priority"
    )


def test_kt_adapter_does_not_trust_client_gateway_source_as_transport():
    from foundation.identity import (
        ActorIdentity,
        Principal,
        RecipientIdentity,
        resolve_chat_stream_identity,
    )
    from foundation.message_contract import (
        GatewayMetadata,
        InboundMessageContract,
        TextContent,
    )
    from nanobot_kt.message_adapter import build_kt_message_invocation

    message = InboundMessageContract(
        message_id="m-source-spoof",
        chat_stream=resolve_chat_stream_identity(
            platform="external_web",
            chat_type="private",
            session_id="private_user-one",
        ),
        actor=ActorIdentity(platform="external_web", actor_id="user-one"),
        recipient=RecipientIdentity(
            platform="external_web",
            recipient_type="user",
            recipient_id="user-one",
        ),
        principal=Principal(
            platform="external_web",
            owner_type="user",
            owner_id="user-one",
        ),
        text="测试",
        parts=(TextContent("测试"),),
        gateway=GatewayMetadata(source="agent_link"),
    )

    invocation = build_kt_message_invocation(
        message,
        content="测试",
        runtime_user_id="user-one",
        runtime_session_id="private_user-one",
        sender_name="用户",
    )

    assert invocation.metadata["gateway_transport"] == "external_web"

    with pytest.raises(ValueError, match="principal"):
        build_kt_message_invocation(
            message,
            content="测试",
            runtime_user_id="other-user",
            runtime_session_id="private_user-one",
            sender_name="用户",
        )


def test_chat_adapter_rejects_control_character_gateway_metadata():
    from api.chat_request_contract import (
        ChatProxyRequest,
        normalize_request_client_meta,
        normalize_request_message_contract,
    )

    req = ChatProxyRequest(
        user_id="u-invalid-meta",
        session_id="private_u-invalid-meta",
        query="你好",
        client_meta={
            "platform": "qq",
            "trace": {"source": "web\x00gateway"},
        },
    )
    normalize_request_client_meta(req, expected_chat_type="private")

    with pytest.raises(Exception) as exc:
        normalize_request_message_contract(req)

    assert getattr(exc.value, "status_code", None) == 400
    detail = str(getattr(exc.value, "detail", ""))
    assert "invalid_gateway_source" in detail
    assert "web\x00gateway" not in detail


@pytest.mark.asyncio
async def test_group_route_rejects_control_character_bot_alias_before_service(
    db_session,
    monkeypatch,
):
    from api import group_message_routes

    class ForbiddenService:
        def __init__(self, *args, **kwargs):
            raise AssertionError("非法 gateway metadata 不得进入业务服务")

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        ForbiddenService,
    )

    with pytest.raises(Exception) as exc:
        await group_message_routes.group_message(
            group_message_routes.GroupMessageRequest(
                group_id="42",
                sender_id="u-1",
                message_id="m-invalid-alias",
                message="你好",
                bot_aliases=["Nanobot\x00evil"],
            ),
            db=db_session,
            background_tasks=None,
            _auth=None,
        )

    assert getattr(exc.value, "status_code", None) == 400
    detail = str(getattr(exc.value, "detail", ""))
    assert "invalid_gateway_bot_alias" in detail
    assert "Nanobot\x00evil" not in detail


@pytest.mark.asyncio
async def test_group_route_rejects_unknown_segment_before_service(
    db_session,
    monkeypatch,
):
    from api import group_message_routes

    class ForbiddenService:
        def __init__(self, *args, **kwargs):
            raise AssertionError("非法内容段不得进入业务服务")

    monkeypatch.setattr(
        "app.group_ingress.service.GroupIngressService",
        ForbiddenService,
    )

    with pytest.raises(Exception) as exc:
        await group_message_routes.group_message(
            group_message_routes.GroupMessageRequest(
                group_id="42",
                sender_id="u-1",
                message_id="m-unknown",
                segments=[
                    {"type": "future_magic", "data": {}},
                ],
            ),
            db=db_session,
            background_tasks=None,
            _auth=None,
        )

    assert getattr(exc.value, "status_code", None) == 400
    assert "unsupported_content_part" in str(
        getattr(exc.value, "detail", "")
    )


@pytest.mark.asyncio
async def test_agent_dispatch_prefers_typed_gateway_port():
    from api.chat_request_contract import (
        ChatProxyRequest,
        build_chat_message_contract,
        normalize_request_client_meta,
    )
    from core.agent_runtime import dispatch_agent_message

    req = ChatProxyRequest(
        user_id="u-typed",
        session_id="private_u-typed",
        query="你好",
        message_id="m-typed",
        client_meta={"platform": "qq"},
    )
    normalize_request_client_meta(req, expected_chat_type="private")
    message = build_chat_message_contract(req)
    calls = []

    class TypedGateway:
        async def handle_message_contract(self, contract, **kwargs):
            calls.append((contract, kwargs))
            return "typed"

        async def handle_message(self, *args, **kwargs):
            raise AssertionError("类型化 Port 存在时不得走旧签名")

    result = await dispatch_agent_message(
        TypedGateway(),
        message,
        content="<user_input>你好</user_input>",
        runtime_user_id="u-typed",
        runtime_session_id="private_u-typed",
        sender_name="小明",
        metadata={"platform": "evil"},
    )

    assert result == "typed"
    assert calls[0][0] is message
    assert calls[0][1]["runtime_session_id"] == "private_u-typed"


@pytest.mark.asyncio
async def test_agent_dispatch_does_not_treat_mock_dynamic_attribute_as_typed_port():
    from unittest.mock import AsyncMock

    from api.chat_request_contract import (
        ChatProxyRequest,
        build_chat_message_contract,
        normalize_request_client_meta,
    )
    from core.agent_runtime import dispatch_agent_message

    req = ChatProxyRequest(
        user_id="u-legacy-mock",
        session_id="private_u-legacy-mock",
        query="你好",
        message_id="m-legacy-mock",
        client_meta={"platform": "qq"},
    )
    normalize_request_client_meta(req, expected_chat_type="private")
    message = build_chat_message_contract(req)
    gateway = AsyncMock()
    gateway.handle_message = AsyncMock(return_value="legacy")

    result = await dispatch_agent_message(
        gateway,
        message,
        content="<user_input>你好</user_input>",
        runtime_user_id="u-legacy-mock",
        runtime_session_id="private_u-legacy-mock",
        sender_name="用户",
    )

    assert result == "legacy"
    gateway.handle_message.assert_awaited_once()


def test_production_bridge_classes_install_message_contract_adapter():
    from nanobot_kt.bridge import NanobotBridge, NanobotBridgePool
    from nanobot_kt.message_adapter import MessageContractBridgeMixin

    assert issubclass(NanobotBridge, MessageContractBridgeMixin)
    assert issubclass(NanobotBridgePool, MessageContractBridgeMixin)


def test_chat_response_path_builds_typed_outbound_contract(monkeypatch):
    from api import chat_response_contract
    from api.chat_request_contract import (
        ChatProxyRequest,
        bind_chat_message_contract,
        normalize_request_client_meta,
    )
    from foundation.message_contract import (
        MessageAction,
        OutboundMessageContract,
    )

    req = ChatProxyRequest(
        user_id="u-outbound",
        session_id="private_u-outbound",
        query="问题",
        client_meta={"platform": "qq"},
    )
    normalize_request_client_meta(req, expected_chat_type="private")
    bind_chat_message_contract(req)
    captured = []

    def fake_render(message, **kwargs):
        captured.append((message, kwargs))
        return {
            "status": kwargs["status"],
            "reply": message.text,
            "messages": [{"type": "text", "text": message.text}],
            "reply_meta": {},
            "meta": dict(kwargs["meta"]),
        }

    monkeypatch.setattr(
        chat_response_contract,
        "render_chat_json",
        fake_render,
        raising=False,
    )

    payload = chat_response_contract.chat_response_payload(
        req,
        status="ok",
        answer="答案",
    )

    message = captured[0][0]
    assert isinstance(message, OutboundMessageContract)
    assert message.action is MessageAction.REPLY
    assert message.recipient.canonical_id == "qq:user:u-outbound"
    assert payload["reply"] == "答案"


def test_group_response_path_builds_typed_outbound_contract(
    monkeypatch,
):
    from app.group_ingress import response_contract
    from app.group_ingress.message_adapter import (
        build_group_message_contract,
    )
    from foundation.message_contract import (
        MessageAction,
        OutboundMessageContract,
    )

    req = SimpleNamespace(
        group_id="42",
        sender_id="u-1",
        sender_name="小明",
        message="问题",
        message_id="m-1",
        session_name="测试群",
        files=None,
        client_meta={"platform": "qq", "chat_type": "group"},
        segments=[],
        mentions=[],
        reply_to=None,
        reply_to_message_id=None,
        reply_to_sender_id=None,
        reply_to_sender_name=None,
        reply_to_content=None,
        bot_aliases=[],
        bot_id="",
        self_id="",
        bot_name="",
        sender_is_bot=False,
    )
    req._message_contract = build_group_message_contract(req)
    completion = response_contract.build_completed_group_response(
        outcome="respond",
        reply="群答案",
        generation=3,
    )
    captured = []

    def fake_render(message, **kwargs):
        captured.append((message, kwargs))
        return {
            "status": "ok",
            "action": "continue",
            "reply": message.text,
            "messages": [{"type": "text", "text": message.text}],
            "reply_meta": {},
            "meta": dict(kwargs["meta"]),
        }

    monkeypatch.setattr(
        response_contract,
        "render_group_json",
        fake_render,
        raising=False,
    )

    payload = response_contract.completed_group_response_payload(
        req,
        completion,
    )

    message = captured[0][0]
    assert isinstance(message, OutboundMessageContract)
    assert message.action is MessageAction.REPLY
    assert message.recipient.canonical_id == "qq:group:42"
    assert payload["reply"] == "群答案"


def test_chat_push_path_builds_typed_outbound_contract(monkeypatch):
    from api import chat_push_envelope
    from api.chat_request_contract import (
        ChatProxyRequest,
        bind_chat_message_contract,
        normalize_request_client_meta,
    )
    from foundation.message_contract import OutboundMessageContract

    req = ChatProxyRequest(
        user_id="u-push",
        session_id="private_u-push",
        query="问题",
        client_meta={"platform": "qq"},
    )
    normalize_request_client_meta(req, expected_chat_type="private")
    bind_chat_message_contract(req)
    captured = []

    def fake_render(message, **kwargs):
        captured.append((message, kwargs))
        return {
            "status": kwargs["status"],
            "reply": message.text,
            "messages": [{"type": "text", "text": message.text}],
            "reply_meta": {},
            "meta": dict(kwargs["meta"]),
        }

    monkeypatch.setattr(
        chat_push_envelope,
        "render_chat_json",
        fake_render,
        raising=False,
    )

    push = chat_push_envelope.build_chat_push_envelope(
        req,
        answer="推送答案",
        platform="qq",
        chat_type="private",
        is_group=False,
    )

    assert isinstance(captured[0][0], OutboundMessageContract)
    assert push.envelope["reply"] == "推送答案"


@pytest.mark.parametrize(
    "relative_path",
    [
        "core/scheduled_task_outbound.py",
        "core/proactive/generation.py",
        "core/proactive/delivery.py",
    ],
)
def test_outbound_producers_use_typed_message_contract(relative_path):
    source = (ROOT / relative_path).read_text(encoding="utf-8")

    assert "build_chat_response_envelope" not in source
    assert "OutboundMessageContract" in source
    assert "render_chat_json" in source
