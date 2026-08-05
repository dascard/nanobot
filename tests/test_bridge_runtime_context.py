import json

import pytest


def _runtime_facts(text: str) -> dict:
    body = text.split("<runtime_context>", 1)[1].split("</runtime_context>", 1)[0]
    return json.loads(body)


def test_bridge_runtime_context_delegates_to_canonical_json():
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge()
    text = bridge._build_runtime_context(
        user_id="group_1001",
        session_id="group_1001",
        sender_name="雀",
        meta={
            "chat_type": "group",
            "is_group": True,
            "group_id": "1001",
            "message_id": "msg-1",
        },
    )

    facts = _runtime_facts(text)
    assert facts["chat_type"] == "group"
    assert facts["group_id"] == "1001"
    assert "current_message_id" not in facts


def test_bridge_runtime_context_keeps_source_message_id_out_of_system_facts():
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge()
    text = bridge._build_runtime_context(
        user_id="group_1001",
        session_id="group_1001",
        sender_name="雀",
        meta={
            "chat_type": "group",
            "is_group": True,
            "group_id": "1001",
            "source_message_ids": ["msg-source"],
        },
    )

    facts = _runtime_facts(text)
    assert facts["chat_type"] == "group"
    assert "current_message_id" not in facts


def test_bridge_trigger_policy_requires_typed_binding_and_exact_owner():
    from core.agent_runtime.contracts import RuntimePrincipal
    from core.trigger_runtime import (
        TriggerContractError,
        TriggerKind,
        build_trigger_envelope,
    )
    from nanobot_kt.bridge_state import prepare_bridge_run_meta

    envelope = build_trigger_envelope(
        kind=TriggerKind.SCHEDULE,
        source_type="scheduled_task",
        source_ref="task:bridge-trigger",
        idempotency_key="task:bridge-trigger:run-1",
        principal=RuntimePrincipal("qq", "user", "user-1"),
        allowed_tools=("inspect_image",),
        max_model_calls=1,
        max_steps=2,
        timeout_seconds=60,
    )
    constraint = envelope.tool_constraint(("inspect_image",))
    metadata = {
        "_trigger_run_binding": constraint.binding,
        "_trigger_tool_constraint": constraint,
        "user_id": "user-1",
    }

    policy, run_meta = prepare_bridge_run_meta(
        metadata,
        sender_name="",
        is_group=False,
        prompt_engine="v2",
        platform="qq",
        chat_type="private",
        group_id="",
        user_id="user-1",
        session_id="user-1",
    )

    assert policy.constraint is constraint
    assert run_meta["_trigger_run_binding"] is constraint.binding
    assert "_trigger_run_binding" not in metadata
    assert "_trigger_tool_constraint" not in metadata

    with pytest.raises(TriggerContractError, match="不得切换资源 owner"):
        prepare_bridge_run_meta(
            {
                "_trigger_run_binding": constraint.binding,
                "_trigger_tool_constraint": constraint,
                "user_id": "user-2",
            },
            sender_name="",
            is_group=False,
            prompt_engine="v2",
            platform="qq",
            chat_type="private",
            group_id="",
            user_id="user-2",
            session_id="user-2",
        )
