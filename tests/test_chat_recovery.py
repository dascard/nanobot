from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from core.database import ChatLog


def _key(message_id: str = "message-1"):
    from core.inbound_idempotency import InboundClaimKey

    return InboundClaimKey("qq", "private", "private_u1", message_id)


def _request(**updates):
    values = {
        "user_id": "u1",
        "session_id": "private_u1",
        "query": "请看看这张图",
        "files": ["img://1"],
        "sender_name": "小明",
        "session_name": "私聊",
        "stream": False,
        "classification_request": False,
        "merged_messages": ["第一条", "第二条"],
        "message_id": "message-1",
        "source_message_ids": ["source-1"],
        "client_meta": {
            "platform": "qq",
            "chat_type": "private",
            "trace": {"request_id": "transport-1", "correlation_id": "trace-1"},
            "unknown_transport": "ignored",
        },
    }
    values.update(updates)
    return SimpleNamespace(**values)


def _completion(reply: str = "原始回复"):
    from api.chat_response_contract import build_completed_inbound_response

    return build_completed_inbound_response(
        outcome="respond",
        reply=reply,
        reply_meta={"send_mode": "quote"},
        reason="answered",
        source="bridge",
        intent="answer",
        guardrail_status="safe",
        unprocessed_logs=3,
    )


def test_private_business_input_ignores_transport_noise_and_is_stable():
    from api.chat_recovery import (
        build_private_business_input,
        private_business_input_sha256,
    )

    first = build_private_business_input(_request(), key=_key())
    retry = build_private_business_input(
        _request(
            stream=True,
            client_meta={
                "platform": "qq",
                "chat_type": "private",
                "trace": {"request_id": "transport-2"},
                "unknown_transport": "changed",
            },
        ),
        key=_key(),
    )

    assert first == retry
    assert private_business_input_sha256(first) == private_business_input_sha256(
        {key: first[key] for key in reversed(first)}
    )
    assert len(private_business_input_sha256(first)) == 64


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("claim", "message_id"), "message-2"),
        (("user_id",), "u2"),
        (("query",), "换一条正文"),
        (("files",), ["img://2"]),
        (("sender_name",), "小红"),
        (("session_name",), "另一个私聊"),
        (("source_message_ids",), ["source-2"]),
        (("classification_request",), True),
        (("merged_messages",), ["另一条"]),
        (("client", "platform"), "wechat"),
    ],
)
def test_private_business_input_hash_changes_for_business_fields(path, replacement):
    from api.chat_recovery import (
        build_private_business_input,
        private_business_input_sha256,
    )

    original = build_private_business_input(_request(), key=_key())
    changed = deepcopy(original)
    target = changed
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = replacement

    assert private_business_input_sha256(changed) != private_business_input_sha256(
        original
    )


def test_private_request_fingerprint_round_trip_and_mismatch():
    from api.chat_recovery import (
        PrivateRequestMismatchError,
        attach_private_request_fingerprint,
        read_private_request_sha256,
        verify_private_request_sha256,
    )

    request_sha256 = "a" * 64
    meta = attach_private_request_fingerprint(
        {"kind": "private_inbound_request"},
        request_sha256,
    )

    assert read_private_request_sha256(meta) == request_sha256
    assert verify_private_request_sha256(
        json.dumps(meta, ensure_ascii=False),
        request_sha256,
    ) == meta
    with pytest.raises(PrivateRequestMismatchError, match="指纹不一致"):
        verify_private_request_sha256(
            json.dumps(meta, ensure_ascii=False),
            "b" * 64,
        )


@pytest.mark.parametrize(
    "meta_json",
    [
        "not-json",
        "[]",
        json.dumps({"kind": "private_inbound_request"}),
        json.dumps({
            "inbound_request": {
                "schema_version": 2,
                "canonicalizer": "private-business-input-v1",
                "sha256": "a" * 64,
            }
        }),
    ],
)
def test_invalid_private_request_marker_fails_closed(meta_json):
    from api.chat_recovery import (
        PrivateRecoveryCorruptError,
        verify_private_request_sha256,
    )

    with pytest.raises(PrivateRecoveryCorruptError):
        verify_private_request_sha256(meta_json, "a" * 64)


def test_private_completion_marker_round_trip_uses_strict_codec():
    from api.chat_recovery import (
        attach_private_completion_recovery,
        decode_private_completion_recovery,
    )

    request_sha256 = "a" * 64
    meta = attach_private_completion_recovery(
        {"kind": "private_inbound_request"},
        key=_key(),
        request_sha256=request_sha256,
        completion=_completion(),
    )

    assert decode_private_completion_recovery(
        meta,
        key=_key(),
        request_sha256=request_sha256,
    ) == _completion()


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
def test_private_completion_marker_corruption_fails_closed(mutate):
    from api.chat_recovery import (
        PrivateRecoveryCorruptError,
        attach_private_completion_recovery,
        decode_private_completion_recovery,
    )

    meta = attach_private_completion_recovery(
        {"kind": "private_inbound_request"},
        key=_key(),
        request_sha256="a" * 64,
        completion=_completion(),
    )
    mutate(meta["inbound_claim_recovery"])

    with pytest.raises(PrivateRecoveryCorruptError):
        decode_private_completion_recovery(
            meta,
            key=_key(),
            request_sha256="a" * 64,
        )


def _journal_meta(request_sha256: str, completion=None) -> str:
    from api.chat_recovery import (
        attach_private_completion_recovery,
        attach_private_request_fingerprint,
    )

    meta = attach_private_request_fingerprint(
        {"kind": "private_inbound_request"},
        request_sha256,
    )
    if completion is not None:
        meta = attach_private_completion_recovery(
            meta,
            key=_key(),
            request_sha256=request_sha256,
            completion=completion,
        )
    return json.dumps(meta, ensure_ascii=False)


def test_load_private_recoverable_completion_validates_assistant_content(db_session):
    from api.chat_recovery import load_private_recoverable_completion

    request_sha256 = "a" * 64
    db_session.add_all([
        ChatLog(
            user_id="u1",
            session_id=_key().session_id,
            role="user",
            content="请看看这张图",
            message_id=_key().message_id,
            meta_json=_journal_meta(request_sha256, _completion()),
        ),
        ChatLog(
            user_id="u1",
            session_id=_key().session_id,
            role="assistant",
            content="原始回复",
            message_id=_key().message_id,
            meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
        ),
    ])
    db_session.commit()

    assert load_private_recoverable_completion(
        db_session,
        key=_key(),
        request_sha256=request_sha256,
    ) == _completion()


@pytest.mark.parametrize("corruption", ["multiple_journals", "reply_mismatch"])
def test_load_private_recoverable_completion_fails_closed(corruption, db_session):
    from api.chat_recovery import (
        PrivateRecoveryCorruptError,
        load_private_recoverable_completion,
    )

    request_sha256 = "a" * 64
    journal = ChatLog(
        user_id="u1",
        session_id=_key().session_id,
        role="user",
        content="请看看这张图",
        message_id=_key().message_id,
        meta_json=_journal_meta(request_sha256, _completion()),
    )
    assistant = ChatLog(
        user_id="u1",
        session_id=_key().session_id,
        role="assistant",
        content="被篡改" if corruption == "reply_mismatch" else "原始回复",
        message_id=_key().message_id,
        meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
    )
    db_session.add_all([journal, assistant])
    if corruption == "multiple_journals":
        db_session.add(ChatLog(
            user_id="u1",
            session_id=_key().session_id,
            role="user",
            content="重复 journal",
            message_id=_key().message_id,
            meta_json=_journal_meta(request_sha256),
        ))
    db_session.commit()

    with pytest.raises(PrivateRecoveryCorruptError):
        load_private_recoverable_completion(
            db_session,
            key=_key(),
            request_sha256=request_sha256,
        )
