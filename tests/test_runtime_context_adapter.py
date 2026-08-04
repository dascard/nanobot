from __future__ import annotations

from core.agent_runtime import (
    RuntimeActorType,
    RuntimeChatType,
    RuntimeOwnerType,
    RuntimePlanKind,
)
from nanobot_kt.runtime_context_adapter import (
    build_fallback_request_runtime_context,
    build_request_runtime_context,
)


class _ToolPlan:
    sha256 = "b" * 64


def test_build_request_runtime_context_keeps_trusted_identity_and_plan_pins():
    context = build_request_runtime_context(
        request_id="request-1",
        platform="qq",
        user_id="user-1",
        group_id="group-1",
        session_id="session-1",
        is_group=True,
        is_super_user=False,
        trace_id="trace-1",
        run_id="run-1",
        turn_id="turn-1",
        correlation_id="correlation-1",
        message_id="message-1",
        capabilities={"supports_stream": True, "supports_image": False},
        prompt_key="chat.default",
        prompt_sha256="a" * 64,
        tool_plan=_ToolPlan(),
    )

    assert context.principal.owner_type is RuntimeOwnerType.GROUP
    assert context.principal.owner_id == "group-1"
    assert context.actor is not None
    assert context.actor.actor_type is RuntimeActorType.USER
    assert context.actor.actor_id == "user-1"
    assert context.chat_type is RuntimeChatType.GROUP
    assert context.turn_id == "turn-1"
    assert context.correlation_id == "correlation-1"
    assert context.capabilities == frozenset({"supports_stream"})
    assert [plan.kind for plan in context.plans] == [
        RuntimePlanKind.PROMPT,
        RuntimePlanKind.TOOL,
    ]


def test_build_fallback_request_runtime_context_is_explicitly_minimal():
    context = build_fallback_request_runtime_context(
        session_id="legacy-session",
        trace_id="trace-legacy",
        run_id="run-legacy",
    )

    assert context.principal.owner_type is RuntimeOwnerType.USER
    assert context.principal.owner_id == "legacy-session"
    assert context.chat_type is RuntimeChatType.PRIVATE
    assert context.actor is None
