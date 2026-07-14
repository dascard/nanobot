from dataclasses import FrozenInstanceError

import pytest


@pytest.mark.parametrize(
    ("platform", "chat_type", "session_id", "expected"),
    [
        (" QQ ", "group", "group_123", "qq:123:group"),
        ("qq", "private", "private_456", "qq:456:private"),
        ("web", "private", "default_session", "web:default_session:private"),
        ("web", "group", "群:研发", "web:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group"),
        ("web", "private", "raw%id", "web:raw%25id:private"),
    ],
)
def test_resolve_chat_stream_identity(platform, chat_type, session_id, expected):
    from core.chat_stream_identity import resolve_chat_stream_identity

    identity = resolve_chat_stream_identity(
        platform=platform,
        chat_type=chat_type,
        session_id=session_id,
    )

    assert identity.chat_stream_id == expected


def test_parse_canonical_identity_round_trips_encoded_external_id():
    from core.chat_stream_identity import parse_canonical_chat_stream_id

    identity = parse_canonical_chat_stream_id(
        "web:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group",
    )

    assert identity.platform == "web"
    assert identity.chat_type == "group"
    assert identity.external_session_id == "群:研发"
    assert identity.encoded_external_session_id == "%E7%BE%A4%3A%E7%A0%94%E5%8F%91"
    assert identity.chat_stream_id == "web:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group"


def test_chat_stream_identity_is_immutable():
    from core.chat_stream_identity import parse_canonical_chat_stream_id

    identity = parse_canonical_chat_stream_id("qq:123:group")

    with pytest.raises(FrozenInstanceError):
        identity.platform = "web"


@pytest.mark.parametrize(
    ("kwargs", "expected_code"),
    [
        (
            {"platform": "qq", "chat_type": "group", "session_id": "private_1"},
            "mismatched_chat_type",
        ),
        (
            {"platform": "qq", "chat_type": "private", "session_id": "qq:1:group"},
            "mismatched_chat_type",
        ),
        (
            {"platform": "web", "chat_type": "group", "session_id": "qq:1:group"},
            "mismatched_platform",
        ),
        (
            {"platform": "bad platform", "chat_type": "private", "session_id": "x"},
            "invalid_platform",
        ),
        (
            {"platform": "qq", "chat_type": "unknown", "session_id": "x"},
            "invalid_chat_type",
        ),
        (
            {"platform": "qq", "chat_type": "private", "session_id": ""},
            "empty_session_id",
        ),
        (
            {"platform": "qq", "chat_type": "private", "session_id": "line\nbreak"},
            "invalid_external_session_id",
        ),
        (
            {"platform": "qq", "chat_type": "private", "session_id": "nul\x00id"},
            "invalid_external_session_id",
        ),
    ],
)
def test_resolve_chat_stream_identity_rejects_ambiguous_or_mismatched_values(
    kwargs,
    expected_code,
):
    from core.chat_stream_identity import (
        ChatStreamIdentityError,
        resolve_chat_stream_identity,
    )

    with pytest.raises(ChatStreamIdentityError) as exc_info:
        resolve_chat_stream_identity(**kwargs)

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize("session_id", ["\nprivate_1", "private_1\t"])
def test_resolve_chat_stream_identity_rejects_outer_control_characters(session_id):
    from core.chat_stream_identity import (
        ChatStreamIdentityError,
        resolve_chat_stream_identity,
    )

    with pytest.raises(ChatStreamIdentityError) as exc_info:
        resolve_chat_stream_identity(
            platform="qq",
            chat_type="private",
            session_id=session_id,
        )

    assert exc_info.value.code == "invalid_external_session_id"


@pytest.mark.parametrize(
    ("value", "expected_code"),
    [
        ("web:%:private", "invalid_percent_encoding"),
        ("web:%2:private", "invalid_percent_encoding"),
        ("web:%GG:private", "invalid_percent_encoding"),
        ("web:%FF:private", "invalid_percent_encoding"),
        ("web:%e7%be%a4:private", "invalid_percent_encoding"),
        ("web:%41:private", "invalid_percent_encoding"),
        ("web::private", "invalid_external_session_id"),
        ("WEB:one:private", "invalid_platform"),
        ("web:one:PRIVATE", "invalid_chat_type"),
        ("web:one:unknown", "invalid_chat_type"),
        ("web:one:private:extra", "invalid_canonical_id"),
        (" web:one:private", "invalid_platform"),
    ],
)
def test_parse_canonical_identity_rejects_malformed_or_noncanonical_values(
    value,
    expected_code,
):
    from core.chat_stream_identity import (
        ChatStreamIdentityError,
        parse_canonical_chat_stream_id,
    )

    with pytest.raises(ChatStreamIdentityError) as exc_info:
        parse_canonical_chat_stream_id(value)

    assert exc_info.value.code == expected_code


def test_resolve_chat_stream_identity_encodes_three_segment_raw_id():
    from core.chat_stream_identity import resolve_chat_stream_identity

    identity = resolve_chat_stream_identity(
        platform="web",
        chat_type="private",
        session_id="a:b:c",
    )

    assert identity.chat_stream_id == "web:a%3Ab%3Ac:private"


def test_surrogate_is_rejected_by_resolve_and_canonical_parser():
    from core.chat_stream_identity import (
        ChatStreamIdentityError,
        parse_canonical_chat_stream_id,
        resolve_chat_stream_identity,
    )

    surrogate = chr(0xD800)
    with pytest.raises(ChatStreamIdentityError) as resolve_error:
        resolve_chat_stream_identity(
            platform="qq",
            chat_type="group",
            session_id=surrogate,
        )
    with pytest.raises(ChatStreamIdentityError) as parse_error:
        parse_canonical_chat_stream_id(f"qq:{surrogate}:group")

    assert resolve_error.value.code == "invalid_external_session_id"
    assert parse_error.value.code == "invalid_external_session_id"


def test_legacy_surrogate_alias_returns_none():
    from core.chat_stream_identity import canonicalize_legacy_chat_stream_id

    assert canonicalize_legacy_chat_stream_id("group_" + chr(0xD800)) is None


def test_resolve_canonical_identity_accepts_normalized_explicit_context():
    from core.chat_stream_identity import resolve_chat_stream_identity

    identity = resolve_chat_stream_identity(
        platform=" QQ ",
        chat_type=" GROUP ",
        session_id="qq:123:group",
    )

    assert identity.chat_stream_id == "qq:123:group"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("group_123", "qq:123:group"),
        (" private_456 ", "qq:456:private"),
        ("123", None),
        ("qq:123:group", None),
        ("", None),
    ],
)
def test_canonicalize_legacy_chat_stream_id_only_converts_explicit_aliases(
    value,
    expected,
):
    from core.chat_stream_identity import canonicalize_legacy_chat_stream_id

    assert canonicalize_legacy_chat_stream_id(value) == expected


def test_expression_memory_normalizer_uses_canonical_identity_contract():
    from core.chat_stream_identity import ChatStreamIdentityError
    from core.expression_memory import normalize_chat_stream_id

    assert normalize_chat_stream_id("private_456", chat_type="private") == (
        "qq:456:private"
    )
    assert normalize_chat_stream_id("群:研发", chat_type="group", platform="web") == (
        "web:%E7%BE%A4%3A%E7%A0%94%E5%8F%91:group"
    )
    with pytest.raises(ChatStreamIdentityError):
        normalize_chat_stream_id("qq:456:group", chat_type="private", platform="qq")
