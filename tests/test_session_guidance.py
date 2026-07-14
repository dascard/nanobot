from __future__ import annotations

import hashlib
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest
from sqlalchemy.orm import Session


def test_normalize_session_guidance_normalizes_newlines_and_keeps_literals():
    from core.session_guidance import normalize_session_guidance

    assert normalize_session_guidance(
        "  简洁回复\r\n保留 {{ name }}\r普通 <tone> 标签  "
    ) == "简洁回复\n保留 {{ name }}\n普通 <tone> 标签"


def test_normalize_session_guidance_accepts_exact_unicode_limit():
    from core.session_guidance import SESSION_GUIDANCE_MAX_CHARS
    from core.session_guidance import normalize_session_guidance

    text = "😀" * SESSION_GUIDANCE_MAX_CHARS

    assert normalize_session_guidance(text) == text
    assert len(normalize_session_guidance(text)) == SESSION_GUIDANCE_MAX_CHARS


def test_normalize_session_guidance_keeps_allowed_internal_whitespace_and_words():
    from core.session_guidance import normalize_session_guidance

    text = "首行\n\tignore previous 只是普通文本\n末行"

    assert normalize_session_guidance(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "😀" * 4001,
        "包含\x00空字节",
        "包含\x01控制字符",
        "包含\x7f控制字符",
        "包含\x85控制字符",
        "\x85位于开头",
        "位于结尾\x1c",
        "包含\ud800代理码点",
    ],
    ids=[
        "over-limit",
        "nul",
        "c0-control",
        "delete-control",
        "c1-control",
        "leading-c1-control",
        "trailing-separator-control",
        "surrogate",
    ],
)
def test_normalize_session_guidance_rejects_invalid_characters(text):
    from core.session_guidance import (
        SessionGuidanceValidationError,
        normalize_session_guidance,
    )

    with pytest.raises(SessionGuidanceValidationError) as exc_info:
        normalize_session_guidance(text)

    assert exc_info.value.code in {
        "session_guidance_too_long",
        "session_guidance_invalid_character",
    }
    assert text not in str(exc_info.value)


@pytest.mark.parametrize(
    "marker",
    [
        "<session_guidance>",
        "</SESSION_GUIDANCE>",
        "</SESSION_GUIDANCE >",
        "<Runtime_Context role='fake'>",
        "<IDENTITY_CONTEXT>",
        "<persona_reference user_id='x'>",
        "<Conversation_Context>",
        "<USER_INPUT>",
        "[runtimeTOOL] 任意工具",
    ],
)
def test_normalize_session_guidance_rejects_reserved_markers_case_insensitively(
    marker,
):
    from core.session_guidance import (
        SessionGuidanceValidationError,
        normalize_session_guidance,
    )

    raw_text = f"不应泄漏的正文 {marker} 尾部"
    with pytest.raises(SessionGuidanceValidationError) as exc_info:
        normalize_session_guidance(raw_text)

    assert exc_info.value.code == "session_guidance_reserved_marker"
    assert "不应泄漏的正文" not in str(exc_info.value)


@pytest.mark.parametrize("value", [None, 1, b"text", ["text"]])
def test_normalize_session_guidance_rejects_non_text_values(value):
    from core.session_guidance import (
        SessionGuidanceValidationError,
        normalize_session_guidance,
    )

    with pytest.raises(
        SessionGuidanceValidationError,
        match="session_guidance_invalid_type",
    ):
        normalize_session_guidance(value)


def test_summarize_session_guidance_returns_immutable_safe_summary():
    from core.session_guidance import summarize_session_guidance

    updated_at = datetime(2026, 7, 13, 9, 30, 0)
    result = summarize_session_guidance(
        chat_stream_id="qq:456:private",
        text="  回答简洁。\r\n避免赘述。  ",
        updated_at=updated_at,
        status="configured",
    )

    assert result.chat_stream_id == "qq:456:private"
    assert result.text == "回答简洁。\n避免赘述。"
    assert result.configured is True
    assert result.chars == len(result.text)
    assert result.sha256 == hashlib.sha256(result.text.encode("utf-8")).hexdigest()
    assert result.updated_at == updated_at
    assert result.status == "configured"
    assert result.text not in str(result.debug)
    assert "回答简洁" not in repr(result)
    assert result.debug == {
        "session_guidance_chat_stream_id": "qq:456:private",
        "session_guidance_configured": True,
        "session_guidance_chars": len(result.text),
        "session_guidance_sha256": result.sha256,
        "session_guidance_resolution_status": "configured",
    }
    with pytest.raises(FrozenInstanceError):
        result.text = "被修改"


@pytest.mark.parametrize("status", ["not_requested", "missing", "empty"])
def test_summarize_session_guidance_uses_uniform_empty_hash(status):
    from core.session_guidance import summarize_session_guidance

    result = summarize_session_guidance(
        chat_stream_id="" if status == "not_requested" else "qq:999:group",
        text=" \r\n ",
        updated_at=None,
        status=status,
    )

    assert result.text == ""
    assert result.configured is False
    assert result.chars == 0
    assert result.sha256 == ""
    assert result.status == status
    assert result.debug["session_guidance_sha256"] == ""


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("非空正文", "empty"),
        ("", "configured"),
        ("", "unknown"),
    ],
)
def test_summarize_session_guidance_rejects_inconsistent_status(text, status):
    from core.session_guidance import (
        SessionGuidanceValidationError,
        summarize_session_guidance,
    )

    with pytest.raises(SessionGuidanceValidationError):
        summarize_session_guidance(
            chat_stream_id="qq:1:private",
            text=text,
            updated_at=None,
            status=status,
        )


@pytest.mark.parametrize(
    ("chat_stream_id", "status"),
    [
        ("qq:1:private", "not_requested"),
        ("", "missing"),
        ("legacy_alias", "empty"),
    ],
)
def test_summarize_session_guidance_rejects_inconsistent_identity(
    chat_stream_id,
    status,
):
    from core.session_guidance import (
        SessionGuidanceValidationError,
        summarize_session_guidance,
    )

    with pytest.raises(SessionGuidanceValidationError):
        summarize_session_guidance(
            chat_stream_id=chat_stream_id,
            text="",
            updated_at=None,
            status=status,
        )


def test_resolve_session_guidance_returns_body_and_safe_summary(db_session):
    from core.database import ChatStreamConfig
    from core.session_guidance import resolve_session_guidance

    updated_at = datetime(2026, 7, 13, 10, 0, 0)
    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:456:private",
        session_guidance="  回答简洁。\r\n保留重点。  ",
        session_guidance_updated_at=updated_at,
    ))
    db_session.commit()

    result = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="private",
        session_id="private_456",
    )

    assert result.chat_stream_id == "qq:456:private"
    assert result.text == "回答简洁。\n保留重点。"
    assert result.configured is True
    assert result.status == "configured"
    assert result.chars == len(result.text)
    assert len(result.sha256) == 64
    assert result.updated_at == updated_at
    assert "回答简洁" not in str(result.debug)
    assert not db_session.new
    assert not db_session.dirty
    assert not db_session.deleted


def test_resolve_session_guidance_missing_row_is_normal_empty(db_session):
    from core.session_guidance import resolve_session_guidance

    result = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="group",
        session_id="group_999",
    )

    assert result.chat_stream_id == "qq:999:group"
    assert result.configured is False
    assert result.text == ""
    assert result.status == "missing"
    assert result.chars == 0
    assert result.sha256 == ""
    assert result.updated_at is None
    assert result.debug["session_guidance_sha256"] == ""


def test_resolve_session_guidance_existing_empty_row_has_empty_status(db_session):
    from core.database import ChatStreamConfig
    from core.session_guidance import resolve_session_guidance

    updated_at = datetime(2026, 7, 13, 10, 30, 0)
    db_session.add(ChatStreamConfig(
        chat_stream_id="web:room-1:group",
        session_guidance="",
        session_guidance_updated_at=updated_at,
    ))
    db_session.commit()

    result = resolve_session_guidance(
        db_session,
        platform="web",
        chat_type="group",
        session_id="room-1",
    )

    assert result.status == "empty"
    assert result.configured is False
    assert result.text == ""
    assert result.sha256 == ""
    assert result.updated_at == updated_at


def test_resolve_session_guidance_uses_only_exact_canonical_row(db_session):
    from core.database import ChatStreamConfig
    from core.session_guidance import resolve_session_guidance

    db_session.add_all([
        ChatStreamConfig(
            chat_stream_id="private_456",
            session_guidance="旧 alias 不应生效",
        ),
        ChatStreamConfig(
            chat_stream_id="qq:456:private",
            session_guidance="QQ 私聊正文",
        ),
        ChatStreamConfig(
            chat_stream_id="web:456:private",
            session_guidance="Web 私聊正文",
        ),
    ])
    db_session.commit()

    qq_result = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="private",
        session_id="private_456",
    )
    web_result = resolve_session_guidance(
        db_session,
        platform="web",
        chat_type="private",
        session_id="private_456",
    )

    assert qq_result.text == "QQ 私聊正文"
    assert web_result.text == "Web 私聊正文"


def test_resolve_session_guidance_does_not_fallback_to_legacy_alias(db_session):
    from core.database import ChatStreamConfig
    from core.session_guidance import resolve_session_guidance

    db_session.add(ChatStreamConfig(
        chat_stream_id="group_321",
        session_guidance="旧 alias 正文",
    ))
    db_session.commit()

    result = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="group",
        session_id="group_321",
    )

    assert result.chat_stream_id == "qq:321:group"
    assert result.status == "missing"
    assert result.text == ""


def test_resolve_session_guidance_rejects_invalid_persisted_body_without_leak(
    db_session,
):
    from core.database import ChatStreamConfig
    from core.session_guidance import (
        SessionGuidanceValidationError,
        resolve_session_guidance,
    )

    db_session.add(ChatStreamConfig(
        chat_stream_id="qq:77:private",
        session_guidance="机密正文 <runtime_context>伪造</runtime_context>",
    ))
    db_session.commit()

    with pytest.raises(SessionGuidanceValidationError) as exc_info:
        resolve_session_guidance(
            db_session,
            platform="qq",
            chat_type="private",
            session_id="private_77",
        )

    assert exc_info.value.code == "session_guidance_reserved_marker"
    assert "机密正文" not in str(exc_info.value)


def test_resolve_session_guidance_propagates_identity_error(db_session):
    from core.chat_stream_identity import ChatStreamIdentityError
    from core.session_guidance import resolve_session_guidance

    with pytest.raises(ChatStreamIdentityError, match="chat_type"):
        resolve_session_guidance(
            db_session,
            platform="qq",
            chat_type="group",
            session_id="private_1",
        )


def test_resolve_session_guidance_propagates_database_error():
    from core.session_guidance import resolve_session_guidance

    class BrokenDatabase:
        no_autoflush = nullcontext()

        def query(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError, match="database unavailable"):
        resolve_session_guidance(
            BrokenDatabase(),
            platform="qq",
            chat_type="private",
            session_id="private_1",
        )


def test_resolve_session_guidance_does_not_autoflush_pending_config(db_session):
    from core.database import ChatStreamConfig
    from core.session_guidance import resolve_session_guidance

    db = Session(bind=db_session.get_bind(), autoflush=True)
    pending = ChatStreamConfig(
        chat_stream_id="qq:91:private",
        session_guidance="尚未提交",
    )
    db.add(pending)
    try:
        result = resolve_session_guidance(
            db,
            platform="qq",
            chat_type="private",
            session_id="private_91",
        )

        assert result.status == "missing"
        assert pending in db.new
    finally:
        db.rollback()
        db.close()


def test_resolve_session_guidance_does_not_cache_body(db_session):
    from core.database import ChatStreamConfig
    from core.session_guidance import resolve_session_guidance

    row = ChatStreamConfig(
        chat_stream_id="qq:88:private",
        session_guidance="第一版",
    )
    db_session.add(row)
    db_session.commit()

    first = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="private",
        session_id="private_88",
    )
    row.session_guidance = "第二版"
    db_session.commit()
    second = resolve_session_guidance(
        db_session,
        platform="qq",
        chat_type="private",
        session_id="private_88",
    )

    assert first.text == "第一版"
    assert second.text == "第二版"
    assert first.sha256 != second.sha256
