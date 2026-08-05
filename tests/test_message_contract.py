from __future__ import annotations

import json

import pytest


def _recipient():
    from foundation.identity import RecipientIdentity

    return RecipientIdentity(
        platform="qq",
        recipient_type="group",
        recipient_id="42",
    )


def test_inbound_message_contract_requires_one_identity_platform():
    from foundation.identity import (
        ActorIdentity,
        Principal,
        RecipientIdentity,
        resolve_chat_stream_identity,
    )
    from foundation.message_contract import (
        InboundMessageContract,
        MessageContractError,
        TextContent,
    )

    with pytest.raises(MessageContractError) as exc:
        InboundMessageContract(
            message_id="m-1",
            chat_stream=resolve_chat_stream_identity(
                platform="qq",
                chat_type="group",
                session_id="group_42",
            ),
            actor=ActorIdentity(platform="web", actor_id="u-1"),
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

    assert exc.value.code == "identity_platform_mismatch"


@pytest.mark.parametrize(
    ("principal_id", "recipient_id"),
    (("42", "99"), ("99", "99")),
)
def test_inbound_message_contract_rejects_cross_scope_owner_ids(
    principal_id,
    recipient_id,
):
    from foundation.identity import (
        ActorIdentity,
        Principal,
        RecipientIdentity,
        resolve_chat_stream_identity,
    )
    from foundation.message_contract import (
        InboundMessageContract,
        MessageContractError,
    )

    with pytest.raises(MessageContractError) as exc:
        InboundMessageContract(
            message_id="m-scope",
            chat_stream=resolve_chat_stream_identity(
                platform="qq",
                chat_type="group",
                session_id="group_42",
            ),
            actor=ActorIdentity(platform="qq", actor_id="u-1"),
            recipient=RecipientIdentity(
                platform="qq",
                recipient_type="group",
                recipient_id=recipient_id,
            ),
            principal=Principal(
                platform="qq",
                owner_type="group",
                owner_id=principal_id,
            ),
            text="你好",
        )

    assert exc.value.code == "identity_scope_mismatch"


def test_unknown_content_part_fails_closed():
    from foundation.message_contract import (
        MessageContractError,
        parse_content_part,
    )

    with pytest.raises(MessageContractError) as exc:
        parse_content_part({"type": "future_magic", "value": "x"})

    assert exc.value.code == "unsupported_content_part"


def test_outbound_contract_rejects_content_for_no_reply():
    from foundation.message_contract import (
        MessageAction,
        MessageContractError,
        OutboundMessageContract,
        TextContent,
    )

    with pytest.raises(MessageContractError) as exc:
        OutboundMessageContract(
            action=MessageAction.NO_REPLY,
            recipient=_recipient(),
            parts=(TextContent("不应发送"),),
        )

    assert exc.value.code == "unexpected_outbound_content"


def test_same_outbound_contract_renders_all_current_transports():
    from core.message_transport_adapters import (
        render_chat_json,
        render_group_json,
        render_qq_message,
        render_sse_event,
    )
    from foundation.message_contract import (
        ImageContent,
        MessageAction,
        OutboundMessageContract,
        TextContent,
    )

    message = OutboundMessageContract(
        action=MessageAction.REPLY,
        recipient=_recipient(),
        parts=(
            TextContent("统一回复"),
            ImageContent("https://example.test/a.png"),
        ),
    )
    meta = {
        "platform": "qq",
        "chat_type": "group",
        "chat_stream_id": "qq:42:group",
    }

    chat = render_chat_json(message, status="ok", meta=meta)
    sse = render_sse_event(message, meta=meta)
    group = render_group_json(message, meta=meta)
    qq = render_qq_message(message)

    assert chat["reply"] == "统一回复"
    assert chat["messages"] == [
        {"type": "text", "text": "统一回复"},
        {"type": "image", "url": "https://example.test/a.png"},
    ]
    assert group["action"] == "continue"
    assert group["messages"] == chat["messages"]
    assert qq.message == (
        "统一回复\n"
        "[CQ:image,file=https://example.test/a.png]"
    )

    assert sse.startswith("data: ")
    event = json.loads(sse.removeprefix("data: ").strip())
    assert event["status"] == "done"
    assert event["messages"] == chat["messages"]
    assert event["meta"] == meta


def test_progress_contract_has_explicit_retraction_semantics():
    from foundation.message_contract import (
        MessageAction,
        MessageContractError,
        MessagePhase,
        OutboundMessageContract,
        RetractPolicy,
        TextContent,
    )

    progress = OutboundMessageContract(
        action=MessageAction.REPLY,
        recipient=_recipient(),
        parts=(TextContent("处理中"),),
        phase=MessagePhase.PROGRESS,
        retract_policy=RetractPolicy.REPLACE_ON_FINAL,
    )
    assert progress.phase is MessagePhase.PROGRESS

    with pytest.raises(MessageContractError) as exc:
        OutboundMessageContract(
            action=MessageAction.REPLY,
            recipient=_recipient(),
            parts=(TextContent("处理中"),),
            phase=MessagePhase.PROGRESS,
            retract_policy=RetractPolicy.NONE,
        )
    assert exc.value.code == "invalid_retract_policy"
