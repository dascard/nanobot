import pytest


def test_safe_response_summary_redacts_before_truncating():
    from core.safe_diagnostics import safe_response_summary

    raw = '{"token":"secret-value","detail":"' + "x" * 5000 + '"}'

    value = safe_response_summary(raw, max_chars=256)

    assert len(value) <= 256
    assert "secret-value" not in value
    assert "[REDACTED]" in value


def test_safe_response_summary_redacts_headers_and_url_credentials():
    from core.safe_diagnostics import safe_response_summary

    raw = (
        "Authorization: Bearer bearer-secret\n"
        "Cookie: session=cookie-secret\n"
        "https://user:url-password@example.test/path?token=query-secret&safe=ok"
    )

    value = safe_response_summary(raw, max_chars=512)

    for secret in (
        "bearer-secret",
        "cookie-secret",
        "url-password",
        "query-secret",
    ):
        assert secret not in value
    assert "example.test" in value
    assert "safe=ok" in value


def test_safe_response_summary_redacts_prefixed_form_secret_fields():
    from core.safe_diagnostics import safe_response_summary

    value = safe_response_summary(
        "client_secret=form-secret&refresh_token=refresh-secret&status=failed",
        max_chars=512,
    )

    assert "form-secret" not in value
    assert "refresh-secret" not in value
    assert "status=failed" in value


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ('{"token":"secret-without-close', "secret-without-close"),
        ("{'api_key':'single-quote-secret", "single-quote-secret"),
        ('refresh_token="truncated-form-secret', "truncated-form-secret"),
    ],
)
def test_safe_response_summary_redacts_truncated_sensitive_values(raw, secret):
    from core.safe_diagnostics import safe_response_summary

    value = safe_response_summary(raw, max_chars=512)

    assert secret not in value
    assert "[REDACTED]" in value


def test_safe_response_summary_rejects_non_positive_limit():
    from core.safe_diagnostics import safe_response_summary

    with pytest.raises(ValueError, match="max_chars"):
        safe_response_summary("ok", max_chars=0)


def test_safe_response_summary_handles_deep_json_without_leaking_secret():
    from core.safe_diagnostics import safe_response_summary

    secret = "deep-safe-diagnostic-secret"
    raw = "[" * 1_200 + '{"token":"' + secret + '"}' + "]" * 1_200

    value = safe_response_summary(raw, max_chars=512)

    assert secret not in value
    assert len(value) <= 512
