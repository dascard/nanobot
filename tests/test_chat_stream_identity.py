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


@pytest.mark.parametrize(
    ("value", "platform", "expected"),
    [
        ("group_123", "qq", "qq:123:group"),
        ("private_456", "web", "web:456:private"),
        ("web:%E7%BE%A4:group", "qq", "web:%E7%BE%A4:group"),
        ("123", "qq", None),
        ("group_", "qq", None),
        ("qq:%GG:group", "qq", None),
    ],
)
def test_parse_compatibility_identity_only_accepts_explicit_storage_forms(
    value,
    platform,
    expected,
):
    from foundation.identity import (
        parse_compatibility_chat_stream_identity,
    )

    identity = parse_compatibility_chat_stream_identity(
        value,
        legacy_platform=platform,
    )

    assert (
        identity.chat_stream_id if identity is not None else None
    ) == expected


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


def test_core_identity_module_is_foundation_compatibility_facade():
    from core.chat_stream_identity import ChatStreamIdentity as CoreIdentity
    from foundation.identity import ChatStreamIdentity

    assert CoreIdentity is ChatStreamIdentity


def test_identity_value_objects_do_not_collide_across_platform_or_chat_type():
    from foundation.identity import resolve_chat_stream_identity

    qq_group = resolve_chat_stream_identity(
        platform="qq",
        chat_type="group",
        session_id="42",
    )
    web_group = resolve_chat_stream_identity(
        platform="web",
        chat_type="group",
        session_id="42",
    )
    qq_private = resolve_chat_stream_identity(
        platform="qq",
        chat_type="private",
        session_id="42",
    )

    assert len({
        qq_group.chat_stream_id,
        web_group.chat_stream_id,
        qq_private.chat_stream_id,
    }) == 3
    assert qq_group.legacy_runtime_session_id == "group_42"
    assert qq_private.legacy_runtime_session_id == "42"


def test_actor_recipient_and_principal_are_validated_value_objects():
    from foundation.identity import (
        ActorIdentity,
        PlatformId,
        Principal,
        RecipientIdentity,
    )

    platform = PlatformId.parse(" QQ ")
    actor = ActorIdentity(platform=platform, actor_id="user-1")
    recipient = RecipientIdentity(
        platform=platform,
        recipient_type="group",
        recipient_id="group-1",
    )
    principal = Principal(
        platform=platform,
        owner_type="user",
        owner_id="user-1",
    )

    assert platform.value == "qq"
    assert actor.canonical_id == "qq:actor:user-1"
    assert recipient.canonical_id == "qq:group:group-1"
    assert principal.canonical_id == "qq:user:user-1"

    with pytest.raises(ValueError):
        RecipientIdentity(
            platform=platform,
            recipient_type="unknown",
            recipient_id="x",
        )
