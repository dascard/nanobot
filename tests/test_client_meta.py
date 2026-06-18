import pytest


def test_normalize_client_meta_defaults_platform_and_chat_type():
    from core.client_meta import normalize_client_meta

    normalized = normalize_client_meta(None, expected_chat_type="private")

    assert normalized["platform"] == "qq"
    assert normalized["chat_type"] == "private"


def test_normalize_client_meta_lowercases_platform_and_preserves_extensions():
    from core.client_meta import normalize_client_meta

    normalized = normalize_client_meta(
        {"platform": " Web ", "stickers": [{"file": "s.png"}]},
        expected_chat_type="group",
    )

    assert normalized["platform"] == "web"
    assert normalized["chat_type"] == "group"
    assert normalized["stickers"] == [{"file": "s.png"}]


def test_normalize_client_meta_rejects_chat_type_mismatch():
    from core.client_meta import ClientMetaValidationError, normalize_client_meta

    with pytest.raises(ClientMetaValidationError, match="chat_type"):
        normalize_client_meta({"chat_type": "group"}, expected_chat_type="private")


def test_normalize_client_meta_trims_trace_request_id():
    from core.client_meta import normalize_client_meta

    normalized = normalize_client_meta(
        {"trace": {"request_id": " req-" + "x" * 200}},
        expected_chat_type="private",
    )

    assert normalized["trace"]["request_id"].startswith("req-")
    assert len(normalized["trace"]["request_id"]) == 128


def test_normalize_client_meta_rejects_non_string_request_id():
    from core.client_meta import ClientMetaValidationError, normalize_client_meta

    with pytest.raises(ClientMetaValidationError, match="trace.request_id"):
        normalize_client_meta(
            {"trace": {"request_id": 123}},
            expected_chat_type="private",
        )
