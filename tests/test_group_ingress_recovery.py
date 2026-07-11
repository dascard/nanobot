from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest


def _key(message_id: str = "message-1"):
    from core.inbound_idempotency import InboundClaimKey

    return InboundClaimKey("qq", "group", "group_123", message_id)


def _business_input() -> dict:
    return {
        "schema_version": 1,
        "claim": {
            "platform": "qq",
            "chat_type": "group",
            "session_id": "group_123",
            "message_id": "message-1",
        },
        "sender": {"id": "u1", "name": "小明", "is_bot": False},
        "session_name": "测试群",
        "message_text": "请看看这张图",
        "segments": [{"type": "image", "data": {"file": "img://1"}}],
        "mentions": [],
        "reply_to": None,
        "directed": {"at_bot": True, "reply_to_bot": False},
        "files": ["img://1"],
        "stickers": [{"file_ref": "img://1", "name": "表情"}],
        "bot": {"self_id": "9", "bot_id": "9", "bot_name": "Nanobot"},
        "bot_aliases": ["Nanobot", "奶宝"],
    }


def _completion(reply: str = "原始回复"):
    from app.group_ingress.response_contract import build_completed_group_response

    return build_completed_group_response(
        outcome="respond",
        reply=reply,
        reply_meta={"quote": "引用"},
        generation=7,
        reason="timing continue",
    )


def test_group_business_input_sha256_is_deterministic_and_key_order_independent():
    from app.group_ingress.recovery import group_business_input_sha256

    first = _business_input()
    reordered = {key: first[key] for key in reversed(first)}

    assert group_business_input_sha256(first) == group_business_input_sha256(reordered)
    assert len(group_business_input_sha256(first)) == 64


def test_build_group_business_input_uses_exact_fields_and_defensive_copies():
    from app.group_ingress.recovery import build_group_business_input

    expected = _business_input()
    message_meta = {
        "sender": deepcopy(expected["sender"]),
        "segments": deepcopy(expected["segments"]),
        "mentions": deepcopy(expected["mentions"]),
        "reply_to": deepcopy(expected["reply_to"]),
        "directed": deepcopy(expected["directed"]),
        "files": deepcopy(expected["files"]),
        "bot": deepcopy(expected["bot"]),
        "raw_message": "不进入指纹",
        "client_meta": {"trace_id": "不进入指纹"},
    }
    sticker_payloads = deepcopy(expected["stickers"])
    req = SimpleNamespace(
        session_name=expected["session_name"],
        bot_aliases=list(expected["bot_aliases"]),
    )

    payload = build_group_business_input(
        req,
        key=_key(),
        message_text=expected["message_text"],
        message_meta=message_meta,
        sticker_payloads=sticker_payloads,
    )

    assert payload == expected
    message_meta["sender"]["id"] = "mutated"
    message_meta["segments"][0]["data"]["file"] = "mutated"
    sticker_payloads[0]["file_ref"] = "mutated"
    req.bot_aliases.append("mutated")
    assert payload == expected


def test_build_group_business_input_rejects_non_json_business_value():
    from app.group_ingress.recovery import build_group_business_input

    with pytest.raises((TypeError, ValueError)):
        build_group_business_input(
            SimpleNamespace(session_name="测试群", bot_aliases=[]),
            key=_key(),
            message_text="hello",
            message_meta={
                "sender": {},
                "segments": [{"type": "text", "data": {"bad": {1, 2}}}],
                "mentions": [],
                "reply_to": None,
                "directed": {},
                "files": [],
                "bot": {},
            },
            sticker_payloads=[],
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("sender", "id"), "u2"),
        (("sender", "name"), "小红"),
        (("message_text",), "换一条正文"),
        (("segments",), [{"type": "text", "data": {"text": "换段"}}]),
        (("reply_to",), {"message_id": "quoted"}),
        (("directed",), {"at_bot": False, "reply_to_bot": False}),
        (("files",), ["img://2"]),
        (("stickers",), [{"file_ref": "img://2"}]),
        (("bot", "bot_id"), "10"),
        (("bot_aliases",), ["另一个名字"]),
    ],
)
def test_group_business_input_sha256_changes_for_every_business_field(path, replacement):
    from app.group_ingress.recovery import group_business_input_sha256

    original = _business_input()
    changed = deepcopy(original)
    target = changed
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement

    assert group_business_input_sha256(changed) != group_business_input_sha256(original)


def test_request_fingerprint_round_trip_and_failed_takeover_verification():
    from app.group_ingress.recovery import (
        GroupRequestMismatchError,
        attach_group_request_fingerprint,
        read_group_request_sha256,
        verify_group_request_sha256,
    )

    request_sha256 = "a" * 64
    meta = attach_group_request_fingerprint({"kind": "ambient"}, request_sha256)

    assert read_group_request_sha256(meta) == request_sha256
    assert verify_group_request_sha256(
        json.dumps(meta, ensure_ascii=False),
        request_sha256,
    ) == meta
    with pytest.raises(GroupRequestMismatchError, match="指纹不一致"):
        verify_group_request_sha256(
            json.dumps(meta, ensure_ascii=False),
            "b" * 64,
        )


@pytest.mark.parametrize(
    "meta_json",
    [
        "not-json",
        "[]",
        json.dumps({"kind": "ambient"}),
        json.dumps({
            "inbound_request": {
                "schema_version": 2,
                "canonicalizer": "group-business-input-v1",
                "sha256": "a" * 64,
            }
        }),
    ],
)
def test_invalid_ambient_request_marker_fails_closed(meta_json):
    from app.group_ingress.recovery import (
        GroupRecoveryCorruptError,
        verify_group_request_sha256,
    )

    with pytest.raises(GroupRecoveryCorruptError):
        verify_group_request_sha256(meta_json, "a" * 64)


def test_completion_marker_round_trip_reuses_strict_completed_response_codec():
    from app.group_ingress.recovery import (
        attach_group_completion_recovery,
        decode_group_completion_recovery,
    )

    request_sha256 = "a" * 64
    reply_meta = attach_group_completion_recovery(
        {"kind": "group_reply", "reply_meta": {"quote": "引用"}},
        key=_key(),
        request_sha256=request_sha256,
        completion=_completion(),
    )
    decoded = decode_group_completion_recovery(
        reply_meta,
        key=_key(),
        request_sha256=request_sha256,
    )

    assert decoded == _completion()
    assert decoded.reply == "原始回复"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda marker: marker.update(schema_version=2),
        lambda marker: marker.update(claim_key_sha256="b" * 64),
        lambda marker: marker.update(request_sha256="c" * 64),
        lambda marker: marker.update(completed_response={"schema_version": 99}),
        lambda marker: marker.update(extra="forbidden"),
    ],
)
def test_completion_marker_corruption_fails_closed(mutate):
    from app.group_ingress.recovery import (
        GroupRecoveryCorruptError,
        attach_group_completion_recovery,
        decode_group_completion_recovery,
    )

    request_sha256 = "a" * 64
    meta = attach_group_completion_recovery(
        {"kind": "group_reply"},
        key=_key(),
        request_sha256=request_sha256,
        completion=_completion(),
    )
    mutate(meta["inbound_claim_recovery"])

    with pytest.raises(GroupRecoveryCorruptError):
        decode_group_completion_recovery(
            meta,
            key=_key(),
            request_sha256=request_sha256,
        )


def test_persist_group_bridge_reply_atomically_stores_message_and_recovery(
    db_session,
):
    from app.group_ingress.helpers import persist_group_bridge_reply, safe_meta
    from app.group_ingress.recovery import decode_group_completion_recovery
    from core.database import ChatLog, ConversationTurn

    key = _key("persisted-message")
    completion = _completion("数据库中的原始回复")
    persist_group_bridge_reply(
        db_session,
        group_user_id="group_123",
        sender_name="小明",
        session_name="测试群",
        query="请回复",
        answer="数据库中的原始回复",
        bot_name="测试Bot",
        message_id=key.message_id,
        source_message_ids=[key.message_id],
        reply_meta={"quote": "引用", "_internal": object()},
        claim_key=key,
        request_sha256="a" * 64,
        completion=completion,
    )

    assistant = db_session.query(ChatLog).filter_by(role="assistant").one()
    assert assistant.message_id == "persisted-message"
    assert assistant.content == "数据库中的原始回复"
    assert safe_meta(assistant.meta_json)["reply_meta"] == {"quote": "引用"}
    decoded = decode_group_completion_recovery(
        safe_meta(assistant.meta_json),
        key=key,
        request_sha256="a" * 64,
    )
    assert decoded == completion
    assert db_session.query(ConversationTurn).count() == 2


def test_reply_persistence_commit_failure_rolls_back_log_turns_and_marker(
    db_session,
    monkeypatch,
):
    from app.group_ingress.helpers import persist_group_bridge_reply
    from core.database import ChatLog, ConversationTurn

    error = RuntimeError("reply commit failed")

    def fail_commit() -> None:
        raise error

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError) as raised:
        persist_group_bridge_reply(
            db_session,
            group_user_id="group_123",
            sender_name="小明",
            session_name="测试群",
            query="请回复",
            answer="不会半提交",
            message_id="rollback-message",
            claim_key=_key("rollback-message"),
            request_sha256="a" * 64,
            completion=_completion("不会半提交"),
        )

    assert raised.value is error
    db_session.rollback()
    assert db_session.query(ChatLog).count() == 0
    assert db_session.query(ConversationTurn).count() == 0


def test_load_recoverable_completion_returns_one_and_rejects_ambiguous_rows(
    db_session,
):
    from app.group_ingress.helpers import persist_group_bridge_reply
    from app.group_ingress.recovery import (
        GroupRecoveryCorruptError,
        load_group_recoverable_completion,
    )

    request_sha256 = "a" * 64
    key = _key("load-message")
    assert load_group_recoverable_completion(
        db_session,
        key=_key("missing-message"),
        request_sha256=request_sha256,
    ) is None

    persist_group_bridge_reply(
        db_session,
        group_user_id=key.session_id,
        sender_name="小明",
        session_name="测试群",
        query="query",
        answer="reply",
        message_id=key.message_id,
        claim_key=key,
        request_sha256=request_sha256,
        completion=_completion("reply"),
    )
    assert load_group_recoverable_completion(
        db_session,
        key=key,
        request_sha256=request_sha256,
    ) == _completion("reply")

    persist_group_bridge_reply(
        db_session,
        group_user_id=key.session_id,
        sender_name="小明",
        session_name="测试群",
        query="query",
        answer="reply again",
        message_id=key.message_id,
        claim_key=key,
        request_sha256=request_sha256,
        completion=_completion("reply again"),
    )
    with pytest.raises(GroupRecoveryCorruptError, match="多个"):
        load_group_recoverable_completion(
            db_session,
            key=key,
            request_sha256=request_sha256,
        )


@pytest.mark.parametrize(
    ("failure_mode", "expected_message"),
    [
        ("missing-marker", "缺少 recovery marker"),
        ("invalid-json", "meta_json 损坏"),
        ("identity-mismatch", "identity 不匹配"),
        ("outcome-mismatch", "respond"),
        ("reply-mismatch", "reply"),
    ],
)
def test_load_recoverable_completion_fails_closed_for_corrupt_candidate(
    db_session,
    failure_mode,
    expected_message,
):
    from app.group_ingress.recovery import (
        GroupRecoveryCorruptError,
        attach_group_completion_recovery,
        load_group_recoverable_completion,
    )
    from core.database import ChatLog

    key = _key(f"corrupt-{failure_mode}")
    if failure_mode == "invalid-json":
        meta_json = "not-json"
    else:
        meta = {"kind": "group_reply"}
        if failure_mode == "identity-mismatch":
            meta = attach_group_completion_recovery(
                meta,
                key=_key("different-message"),
                request_sha256="a" * 64,
                completion=_completion("reply"),
            )
        elif failure_mode == "outcome-mismatch":
            from app.group_ingress.response_contract import (
                build_completed_group_response,
            )

            meta = attach_group_completion_recovery(
                meta,
                key=key,
                request_sha256="a" * 64,
                completion=build_completed_group_response(outcome="no_reply"),
            )
        elif failure_mode == "reply-mismatch":
            meta = attach_group_completion_recovery(
                meta,
                key=key,
                request_sha256="a" * 64,
                completion=_completion("marker reply"),
            )
        meta_json = json.dumps(meta, ensure_ascii=False)

    db_session.add(
        ChatLog(
            user_id=key.session_id,
            session_id=key.session_id,
            role="assistant",
            content="reply",
            message_id=key.message_id,
            meta_json=meta_json,
        )
    )
    db_session.commit()

    with pytest.raises(GroupRecoveryCorruptError, match=expected_message):
        load_group_recoverable_completion(
            db_session,
            key=key,
            request_sha256="a" * 64,
        )


@pytest.mark.parametrize(
    ("failure_mode", "expected_message"),
    [
        ("missing-claim", "claim_key"),
        ("missing-completion", "completion"),
        ("missing-request-sha", "request_sha256"),
        ("private-claim", "chat_type"),
        ("session-mismatch", "identity"),
        ("message-mismatch", "identity"),
        ("non-respond", "Bridge reply"),
        ("reply-mismatch", "Bridge reply"),
    ],
)
def test_persist_group_bridge_reply_rejects_invalid_recovery_contract(
    db_session,
    failure_mode,
    expected_message,
):
    from app.group_ingress.helpers import persist_group_bridge_reply
    from app.group_ingress.response_contract import build_completed_group_response
    from core.inbound_idempotency import InboundClaimKey

    message_id = "strict-message"
    claim_key = _key(message_id)
    request_sha256 = "a" * 64
    completion = _completion("reply")

    if failure_mode == "missing-claim":
        claim_key = None
    elif failure_mode == "missing-completion":
        completion = None
    elif failure_mode == "missing-request-sha":
        request_sha256 = ""
    elif failure_mode == "private-claim":
        claim_key = InboundClaimKey("qq", "private", "group_123", message_id)
    elif failure_mode == "session-mismatch":
        claim_key = InboundClaimKey("qq", "group", "group_other", message_id)
    elif failure_mode == "message-mismatch":
        claim_key = _key("different-message")
    elif failure_mode == "non-respond":
        completion = build_completed_group_response(outcome="no_reply")
    elif failure_mode == "reply-mismatch":
        completion = _completion("different reply")

    with pytest.raises(ValueError, match=expected_message):
        persist_group_bridge_reply(
            db_session,
            group_user_id="group_123",
            sender_name="小明",
            session_name="测试群",
            query="query",
            answer="reply",
            message_id=message_id,
            claim_key=claim_key,
            request_sha256=request_sha256,
            completion=completion,
        )


@pytest.mark.parametrize(
    "value",
    [
        {"not": {1, 2}},
        {"nan": float("nan")},
        ["not-a-mapping"],
    ],
)
def test_group_business_input_sha256_rejects_non_json_or_non_mapping(value):
    from app.group_ingress.recovery import group_business_input_sha256

    with pytest.raises((TypeError, ValueError)):
        group_business_input_sha256(value)
