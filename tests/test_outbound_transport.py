from __future__ import annotations

import asyncio
import errno
import importlib
import json
import logging
import socket
import ssl
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from urllib.parse import quote, quote_plus, unquote_plus

import aiohttp
import pytest


OMITTED_RESPONSE_SUMMARY = "响应正文已省略"


class _FakeContent:
    def __init__(
        self,
        chunks: list[bytes | Exception] | None = None,
        *,
        repeat: bytes | None = None,
    ) -> None:
        self._chunks = list(chunks or [])
        self._repeat = repeat
        self.read_count = 0
        self.requested_sizes: list[int] = []

    def iter_chunked(self, size: int):
        self.requested_sizes.append(size)

        async def _iterate():
            for item in self._chunks:
                if isinstance(item, Exception):
                    raise item
                self.read_count += 1
                yield item
            while self._repeat is not None:
                self.read_count += 1
                yield self._repeat

        return _iterate()


class _FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        body: bytes = b"",
        chunks: list[bytes | Exception] | None = None,
        repeat: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = dict(headers or {})
        self.content = _FakeContent(
            chunks if chunks is not None else [body],
            repeat=repeat,
        )
        self.text_calls = 0

    async def text(self) -> str:
        self.text_calls += 1
        raise AssertionError("结构化 transport 禁止调用 response.text()")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _RaisingContext:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_exc):
        return False


class _FakeSession:
    def __init__(self, result: _FakeResponse | Exception) -> None:
        self._result = result
        self.calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self._result, Exception):
            return _RaisingContext(self._result)
        return self._result


async def _deliver(
    response_or_exc: _FakeResponse | Exception,
    **kwargs,
):
    from core.outbound_transport import deliver_qq_push_with_session

    session = _FakeSession(response_or_exc)
    outcome = await deliver_qq_push_with_session(
        session,
        push_url=kwargs.pop("push_url", "http://qq-push.test/nanobot/push"),
        push_token=kwargs.pop("push_token", "push-token-test-sentinel"),
        target_type=kwargs.pop("target_type", "private"),
        target_id=kwargs.pop("target_id", "target-1"),
        message=kwargs.pop("message", "测试消息"),
        timeout_seconds=kwargs.pop("timeout_seconds", 5),
        **kwargs,
    )
    return outcome, session


@pytest.mark.asyncio
@pytest.mark.parametrize("status", range(200, 300))
async def test_all_2xx_responses_are_structured_success(status):
    response = _FakeResponse(status)

    outcome, _ = await _deliver(response)

    assert outcome.category == "success"
    assert outcome.error_type == ""
    assert outcome.status_code == status
    assert outcome.transport_phase == "response_received"
    assert outcome.retry_after_seconds is None
    assert response.text_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", range(300, 400))
async def test_all_3xx_responses_are_endpoint_failures(status):
    outcome, _ = await _deliver(_FakeResponse(status))

    assert outcome.category == "endpoint"
    assert outcome.error_type == "unexpected_redirect"
    assert outcome.status_code == status
    assert outcome.transport_phase == "response_received"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", range(100, 200))
async def test_all_1xx_responses_remain_explicitly_ambiguous(status):
    outcome, _ = await _deliver(_FakeResponse(status))

    assert outcome.category == "ambiguous"
    assert outcome.error_type == "unexpected_http_status"
    assert outcome.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0, 99, 600, 700, 999])
async def test_out_of_contract_http_status_is_not_forwarded_to_state_machine(status):
    outcome, _ = await _deliver(_FakeResponse(status))

    assert outcome.category == "ambiguous"
    assert outcome.error_type == "invalid_http_status"
    assert outcome.status_code is None
    assert outcome.transport_phase == "response_received"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 425, 429])
async def test_transient_client_statuses_are_not_permanent(status):
    outcome, _ = await _deliver(_FakeResponse(status))

    assert outcome.category == "transient"
    assert outcome.status_code == status
    assert outcome.transport_phase == "response_received"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [status for status in range(400, 500) if status not in {408, 425, 429}],
)
async def test_other_4xx_responses_are_fail_closed(status):
    outcome, _ = await _deliver(_FakeResponse(status))

    assert outcome.category in {
        "endpoint",
        "destination",
        "payload",
        "payload_contract",
    }
    assert outcome.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [status for status in range(500, 600) if status not in {501, 505}],
)
async def test_retryable_server_statuses_are_transient(status):
    outcome, _ = await _deliver(_FakeResponse(status))

    assert outcome.category == "transient"
    assert outcome.status_code == status


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category", "error_type"),
    [
        (400, "payload", "bad_request"),
        (401, "endpoint", "unauthorized"),
        (403, "endpoint", "forbidden"),
        (404, "endpoint", "route_missing"),
        (405, "endpoint", "method_not_allowed"),
        (408, "transient", "request_timeout"),
        (410, "endpoint", "route_gone"),
        (413, "payload", "payload_too_large"),
        (415, "endpoint", "unsupported_media_type"),
        (422, "payload", "unprocessable_payload"),
        (425, "transient", "too_early"),
        (429, "transient", "rate_limited"),
        (500, "transient", "internal_server_error"),
        (501, "endpoint", "not_implemented"),
        (502, "transient", "bad_gateway"),
        (503, "transient", "service_unavailable"),
        (504, "transient", "gateway_timeout"),
        (505, "endpoint", "http_version_not_supported"),
    ],
)
async def test_named_http_status_classification(status, category, error_type):
    outcome, _ = await _deliver(_FakeResponse(status))

    assert outcome.category == category
    assert outcome.error_type == error_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "category"),
    [
        (404, "destination_missing", "destination"),
        (404, "destination_rejected", "destination"),
        (410, "destination_deleted", "destination"),
        (400, "schema_contract_mismatch", "payload_contract"),
        (415, "unsupported_envelope", "payload_contract"),
        (422, "unsupported_schema_version", "payload_contract"),
    ],
)
async def test_fixed_structured_error_codes_refine_http_category(
    status,
    code,
    category,
):
    body = ('{"error":{"code":"' + code + '"}}').encode()

    outcome, _ = await _deliver(_FakeResponse(status, body=body))

    assert outcome.category == category
    assert outcome.error_type == code


@pytest.mark.asyncio
async def test_endpoint_status_precedence_ignores_spoofed_destination_code():
    body = b'{"error":{"code":"destination_missing"}}'

    outcome, _ = await _deliver(_FakeResponse(405, body=body))

    assert outcome.category == "endpoint"
    assert outcome.error_type == "method_not_allowed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, "bad_request"),
        (422, "unprocessable_payload"),
    ],
)
async def test_payload_statuses_ignore_spoofed_destination_code(status, error_type):
    body = b'{"error":{"code":"destination_missing"}}'

    outcome, _ = await _deliver(_FakeResponse(status, body=body))

    assert outcome.category == "payload"
    assert outcome.error_type == error_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body", "category", "error_type"),
    [
        (404, b'{"message":"destination_missing"}', "endpoint", "route_missing"),
        (415, b'{"detail":"unsupported_envelope"}', "endpoint", "unsupported_media_type"),
        (422, b'{"message":"schema_contract_mismatch"}', "payload", "unprocessable_payload"),
    ],
)
async def test_free_text_never_changes_failure_scope(
    status,
    body,
    category,
    error_type,
):
    outcome, _ = await _deliver(_FakeResponse(status, body=body))

    assert outcome.category == category
    assert outcome.error_type == error_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        (" 30 ", 30),
        ("0", 0),
        ("-1", None),
        ("1.5", None),
        ("NaN", None),
        ("稍后", None),
    ],
)
async def test_retry_after_delta_seconds_parsing(header, expected):
    headers = {} if header is None else {"Retry-After": header}

    outcome, _ = await _deliver(_FakeResponse(429, headers=headers))

    assert outcome.retry_after_seconds == expected


@pytest.mark.asyncio
async def test_retry_after_is_capped():
    from core.outbound_transport import MAX_RETRY_AFTER_SECONDS

    outcome, _ = await _deliver(
        _FakeResponse(
            429,
            headers={"Retry-After": str(MAX_RETRY_AFTER_SECONDS + 10_000)},
        )
    )

    assert outcome.retry_after_seconds == MAX_RETRY_AFTER_SECONDS


@pytest.mark.asyncio
async def test_extremely_large_retry_after_integer_is_capped_without_error():
    from core.outbound_transport import MAX_RETRY_AFTER_SECONDS

    outcome, _ = await _deliver(
        _FakeResponse(429, headers={"Retry-After": "9" * 10_000})
    )

    assert outcome.category == "transient"
    assert outcome.status_code == 429
    assert outcome.retry_after_seconds == MAX_RETRY_AFTER_SECONDS


@pytest.mark.asyncio
async def test_retry_after_http_date_uses_ceiling_and_cap():
    from core.outbound_transport import MAX_RETRY_AFTER_SECONDS

    now = datetime(2026, 7, 15, 12, 0, 0, 200_000, tzinfo=timezone.utc)
    future = format_datetime(
        (now + timedelta(seconds=31)).replace(microsecond=0),
        usegmt=True,
    )
    far_future = format_datetime(
        now.replace(microsecond=0) + timedelta(days=2),
        usegmt=True,
    )
    past = format_datetime(now.replace(microsecond=0) - timedelta(seconds=1), usegmt=True)

    future_outcome, _ = await _deliver(
        _FakeResponse(429, headers={"Retry-After": future}),
        now=now,
    )
    capped_outcome, _ = await _deliver(
        _FakeResponse(429, headers={"Retry-After": far_future}),
        now=now,
    )
    past_outcome, _ = await _deliver(
        _FakeResponse(429, headers={"Retry-After": past}),
        now=now,
    )

    assert future_outcome.retry_after_seconds == 31
    assert capped_outcome.retry_after_seconds == MAX_RETRY_AFTER_SECONDS
    assert past_outcome.retry_after_seconds == 0


@pytest.mark.asyncio
async def test_retry_after_is_ignored_for_non_429_status():
    outcome, _ = await _deliver(
        _FakeResponse(503, headers={"Retry-After": "30"})
    )

    assert outcome.retry_after_seconds is None


@pytest.mark.asyncio
async def test_post_disables_redirect_following():
    outcome, session = await _deliver(_FakeResponse(302))

    assert outcome.category == "endpoint"
    assert session.calls[0][1]["allow_redirects"] is False


@pytest.mark.asyncio
async def test_qq_push_sends_dedicated_bearer_header():
    token = "push-token-header-sentinel"

    outcome, session = await _deliver(
        _FakeResponse(200),
        push_token=token,
    )

    assert outcome.category == "success"
    assert session.calls[0][1]["headers"] == {
        "Authorization": f"Bearer {token}",
    }


@pytest.mark.parametrize("codepoint", [*range(0x20), 0x7F])
def test_push_token_resolver_rejects_ascii_control_characters(codepoint):
    from core.outbound_transport import (
        QQPushConfigurationError,
        resolve_qq_push_token,
    )

    token = f"push-secret-{chr(codepoint)}-sentinel"

    with pytest.raises(QQPushConfigurationError) as exc_info:
        resolve_qq_push_token({"NANOBOT_PUSH_TOKEN": token})

    assert str(exc_info.value) == "NANOBOT_PUSH_TOKEN 包含非法控制字符"
    assert "push-secret" not in str(exc_info.value)


@pytest.mark.parametrize(
    "control",
    ["\r", "\n", "\t", "\x7f"],
    ids=["cr", "lf", "tab", "del"],
)
@pytest.mark.parametrize("position", ["leading", "trailing", "middle"])
def test_push_token_resolver_rejects_control_characters_at_every_position(
    control,
    position,
):
    from core.outbound_transport import (
        QQPushConfigurationError,
        resolve_qq_push_token,
    )

    marker = "push-token-position-sentinel"
    tokens = {
        "leading": control + marker,
        "trailing": marker + control,
        "middle": "push-token" + control + "position-sentinel",
    }

    with pytest.raises(QQPushConfigurationError) as exc_info:
        resolve_qq_push_token({"NANOBOT_PUSH_TOKEN": tokens[position]})

    assert str(exc_info.value) == "NANOBOT_PUSH_TOKEN 包含非法控制字符"
    assert "push-token" not in repr(exc_info.value)
    assert "position-sentinel" not in repr(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_push_token_never_enters_exception_or_logs(caplog):
    from core.outbound_transport import QQPushConfigurationError

    token_marker = "invalid-push-token-log-sentinel"
    token = f"{token_marker}\x00suffix"

    with caplog.at_level(logging.WARNING, logger="nanobot.outbound_transport"):
        with pytest.raises(QQPushConfigurationError) as exc_info:
            await _deliver(_FakeResponse(200), push_token=token)

    combined = repr(exc_info.value) + "\n" + caplog.text
    assert token_marker not in combined


@pytest.mark.asyncio
async def test_response_body_stops_at_exact_byte_limit_without_text_call():
    response = _FakeResponse(
        400,
        chunks=[b"12345678", b"must-not-be-read"],
    )

    outcome, _ = await _deliver(response, response_body_limit_bytes=8)

    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY
    assert response.content.read_count == 1
    assert response.text_calls == 0


@pytest.mark.asyncio
async def test_single_oversized_chunk_is_sliced_by_bytes():
    response = _FakeResponse(400, chunks=[b"12345678secret-after-limit"])

    outcome, _ = await _deliver(response, response_body_limit_bytes=8)

    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY
    assert response.content.read_count == 1


@pytest.mark.asyncio
async def test_infinite_response_stream_returns_after_bounded_reads():
    response = _FakeResponse(400, chunks=[], repeat=b"abcd")

    outcome, _ = await asyncio.wait_for(
        _deliver(response, response_body_limit_bytes=16),
        timeout=0.5,
    )

    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY
    assert response.content.read_count == 4


@pytest.mark.asyncio
async def test_utf8_character_cut_at_byte_boundary_is_safe():
    response = _FakeResponse(400, body="你".encode("utf-8"))

    outcome, _ = await _deliver(response, response_body_limit_bytes=2)

    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_response_stream_failure_is_ambiguous_without_http_status():
    response = _FakeResponse(
        400,
        chunks=[b'{"detail":"partial', aiohttp.ClientPayloadError("broken body")],
    )

    outcome, _ = await _deliver(response)

    assert outcome.category == "ambiguous"
    assert outcome.error_type == "connection_reset"
    assert outcome.transport_phase == "read"
    assert outcome.status_code is None


def _connector_error(os_error: OSError) -> aiohttp.ClientConnectorError:
    key = SimpleNamespace(host="qq-push.test", port=80, ssl=False)
    return aiohttp.ClientConnectorError(key, os_error)


def _certificate_error() -> aiohttp.ClientConnectorCertificateError:
    key = SimpleNamespace(host="qq-push.test", port=443, ssl=True, is_ssl=True)
    return aiohttp.ClientConnectorCertificateError(
        key,
        ssl.SSLCertVerificationError(1, "certificate verify failed"),
    )


def _connector_ssl_error() -> aiohttp.ClientConnectorSSLError:
    key = SimpleNamespace(host="qq-push.test", port=443, ssl=True, is_ssl=True)
    return aiohttp.ClientConnectorSSLError(key, ssl.SSLError("TLS failed"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exc", "category", "phase", "error_type"),
    [
        (
            aiohttp.ConnectionTimeoutError("connect timeout"),
            "transient",
            "connect",
            "connect_timeout",
        ),
        (
            _connector_error(
                ConnectionRefusedError(errno.ECONNREFUSED, "connection refused")
            ),
            "transient",
            "connect",
            "connection_refused",
        ),
        (
            _connector_error(socket.gaierror(socket.EAI_NONAME, "dns failed")),
            "transient",
            "connect",
            "dns_error",
        ),
        (
            _connector_error(ConnectionResetError(errno.ECONNRESET, "reset")),
            "transient",
            "connect",
            "connection_reset_before_send",
        ),
        (
            _certificate_error(),
            "endpoint",
            "connect",
            "tls_certificate_error",
        ),
        (
            _connector_ssl_error(),
            "endpoint",
            "connect",
            "tls_error",
        ),
        (
            aiohttp.ServerFingerprintMismatch(b"expected", b"actual", "host", 443),
            "endpoint",
            "connect",
            "tls_fingerprint_mismatch",
        ),
        (
            aiohttp.InvalidURL("not-a-url"),
            "endpoint",
            "connect",
            "invalid_url",
        ),
        (
            aiohttp.NonHttpUrlClientError("ftp://example.test/path"),
            "endpoint",
            "connect",
            "invalid_url",
        ),
        (
            aiohttp.ClientOSError(errno.ECONNRESET, "reset"),
            "ambiguous",
            "write",
            "connection_reset",
        ),
        (
            TimeoutError("write timeout"),
            "ambiguous",
            "write",
            "write_timeout",
        ),
        (
            aiohttp.SocketTimeoutError("read timeout"),
            "ambiguous",
            "read",
            "read_timeout",
        ),
        (
            aiohttp.ServerDisconnectedError("server disconnected"),
            "ambiguous",
            "read",
            "connection_reset",
        ),
        (
            aiohttp.ClientPayloadError("payload reset"),
            "ambiguous",
            "read",
            "connection_reset",
        ),
    ],
)
async def test_network_exception_classification(exc, category, phase, error_type):
    outcome, _ = await _deliver(exc)

    assert outcome.category == category
    assert outcome.transport_phase == phase
    assert outcome.error_type == error_type
    assert outcome.status_code is None
    assert outcome.retry_after_seconds is None
    assert isinstance(outcome.duration_ms, int)
    assert outcome.duration_ms >= 0


@pytest.mark.asyncio
async def test_transport_exception_text_never_enters_outcome_or_logs(caplog):
    message_secret = "exception-message-body-sentinel"
    push_token_secret = "push-token-exception-sentinel"
    exc = RuntimeError("remote echoed " + message_secret)

    with caplog.at_level(logging.WARNING, logger="nanobot.outbound_transport"):
        outcome, _ = await _deliver(
            exc,
            message=message_secret,
            push_token=push_token_secret,
        )

    assert outcome.category == "ambiguous"
    assert outcome.error_type == "transport_error"
    assert outcome.safe_summary == "出站传输失败"
    assert message_secret not in repr(outcome)
    assert message_secret not in caplog.text
    assert push_token_secret not in repr(outcome)
    assert push_token_secret not in caplog.text


@pytest.mark.asyncio
async def test_client_os_error_during_response_body_is_read_phase():
    response = _FakeResponse(
        400,
        chunks=[aiohttp.ClientOSError(errno.ECONNRESET, "reset during read")],
    )

    outcome, _ = await _deliver(response)

    assert outcome.category == "ambiguous"
    assert outcome.error_type == "connection_reset"
    assert outcome.transport_phase == "read"
    assert outcome.status_code is None


@pytest.mark.asyncio
async def test_non_http_url_exception_never_exposes_url_credentials(caplog):
    password = "non-http-password-secret"
    query_secret = "non-http-query-secret"
    exc = aiohttp.NonHttpUrlClientError(
        "ftp://user:"
        + password
        + "@example.test/path?token="
        + query_secret
    )

    with caplog.at_level(logging.WARNING, logger="nanobot.outbound_transport"):
        outcome, _ = await _deliver(exc)

    assert outcome.category == "endpoint"
    assert outcome.error_type == "invalid_url"
    assert outcome.safe_summary == "无效的出站 URL"
    assert password not in caplog.text
    assert query_secret not in caplog.text


@pytest.mark.asyncio
async def test_outcome_and_logs_are_bounded_and_secret_free(caplog):
    response_secret = "response-token-sentinel"
    bearer_secret = "bearer-sentinel"
    url_password = "url-password-sentinel"
    query_secret = "query-token-sentinel"
    target_secret = "target-id-sentinel"
    message_secret = "message-body-sentinel"
    push_url_secret = "push-url-password-sentinel"
    body = (
        '{"token":"'
        + response_secret
        + '","target_id":"'
        + target_secret
        + '","message":"'
        + message_secret
        + '","detail":"Authorization: Bearer '
        + bearer_secret
        + " https://user:"
        + url_password
        + "@example.test/path?token="
        + query_secret
        + '"}'
    ).encode()

    with caplog.at_level(logging.WARNING, logger="nanobot.outbound_transport"):
        outcome, _ = await _deliver(
            _FakeResponse(400, body=body),
            push_url=(
                "https://user:"
                + push_url_secret
                + "@qq-push.test/nanobot/push?token=request-query-secret"
            ),
            target_id=target_secret,
            message=message_secret,
        )

    combined = repr(outcome) + "\n" + caplog.text
    for secret in (
        response_secret,
        bearer_secret,
        url_password,
        query_secret,
        target_secret,
        message_secret,
        push_url_secret,
        "request-query-secret",
    ):
        assert secret not in combined
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY
    assert len(outcome.safe_summary) <= 512


@pytest.mark.asyncio
async def test_request_values_echoed_inside_detail_are_redacted():
    target_secret = "detail-target-sentinel"
    message_secret = "detail-message-sentinel"
    body = (
        '{"detail":"failed target='
        + target_secret
        + " message="
        + message_secret
        + '"}'
    ).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        target_id=target_secret,
        message=message_secret,
    )

    assert target_secret not in outcome.safe_summary
    assert message_secret not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_json_escaped_request_values_in_detail_are_redacted():
    target_secret = "unicode-你-target"
    message_secret = 'line-1\n"unicode-你-message"\\end'
    body = json.dumps(
        {"detail": f"target={target_secret} message={message_secret}"},
        ensure_ascii=True,
    ).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        target_id=target_secret,
        message=message_secret,
    )

    assert target_secret not in outcome.safe_summary
    assert message_secret not in outcome.safe_summary
    assert "unicode-你" not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_request_values_used_as_json_keys_are_omitted():
    target_secret = "opaque-target-key"
    message_secret = "opaque-message-key"
    body = json.dumps(
        {
            message_secret: "first",
            "nested": {
                target_secret: "second",
                message_secret: "third",
            },
        }
    ).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        target_id=target_secret,
        message=message_secret,
    )

    assert target_secret not in outcome.safe_summary
    assert message_secret not in outcome.safe_summary
    assert "first" not in outcome.safe_summary
    assert "second" not in outcome.safe_summary
    assert "third" not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_numeric_target_echo_body_is_omitted():
    target_secret = "9876543210"
    numeric_target = int(target_secret)
    body = json.dumps(
        {
            "group_id": numeric_target,
            "nested": [numeric_target, {"detail": numeric_target}],
            "unrelated": numeric_target + 1,
        }
    ).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        target_id=target_secret,
    )

    assert target_secret not in outcome.safe_summary
    assert str(numeric_target + 1) not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_malformed_json_escaped_request_value_is_redacted():
    message_secret = "unicode-你-secret"
    escaped = json.dumps(message_secret, ensure_ascii=True)[1:-1]
    body = ('{"detail":"failed message=' + escaped).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        message=message_secret,
    )

    assert message_secret not in outcome.safe_summary
    assert escaped not in outcome.safe_summary
    assert "unicode-" not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_url_encoded_request_value_in_valid_json_is_redacted():
    message_secret = "message secret/秘密"
    encoded_variants = {
        quote(message_secret, safe=""),
        quote_plus(message_secret, safe=""),
        quote(message_secret, safe="").lower(),
    }

    for encoded in encoded_variants:
        body = json.dumps(
            {"detail": f"https://error.test/path?message={encoded}"}
        ).encode()
        outcome, _ = await _deliver(
            _FakeResponse(400, body=body),
            message=message_secret,
        )

        decoded_summary = unquote_plus(outcome.safe_summary)
        assert message_secret not in decoded_summary
        assert "秘密" not in decoded_summary
        assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_url_encoded_request_value_in_malformed_json_is_redacted():
    message_secret = "message secret/秘密"
    encoded = quote_plus(message_secret, safe="").lower()
    body = (
        '{"detail":"https://error.test/path?message=' + encoded
    ).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        message=message_secret,
    )

    decoded_summary = unquote_plus(outcome.safe_summary)
    assert message_secret not in decoded_summary
    assert "秘密" not in decoded_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        json.dumps(
            {
                "detail": (
                    "https://error.test/path?message="
                    "Line%20A%2F%e7%A7%98%E5%af%86%0a%22Q%22"
                )
            }
        ).encode(),
        (
            '{"detail":"https://error.test/path?message='
            "Line%20A%2F%e7%A7%98%E5%af%86%0a%22Q%22"
        ).encode(),
    ],
)
async def test_mixed_case_percent_encoded_body_is_omitted(body):
    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        message='Line A/秘密\n"Q"',
    )

    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "escaped"),
    [
        ("秘密", r"\u79D8\u5BC6"),
        ("path/a", r"path\/a"),
        ("ABC", r"\u0041\u0042\u0043"),
        ("😀", r"\uD83D\uDE00"),
    ],
)
async def test_noncanonical_json_escaped_body_is_omitted(message, escaped):
    body = ('{"detail":"failed message=' + escaped).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        message=message,
    )

    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_truncated_long_message_echo_is_omitted():
    message = "LONG-PRIVATE-MESSAGE-BEGIN:" + "甲乙丙丁" * 6_000
    outcome, _ = await _deliver(
        _FakeResponse(400, body=message.encode()),
        message=message,
    )

    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY
    assert "LONG-PRIVATE-MESSAGE-BEGIN" not in outcome.safe_summary


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ['"', ":", "token"])
async def test_response_body_is_omitted_for_punctuation_messages(message):
    response_secret = "must-remain-redacted"
    body = json.dumps(
        {"token": response_secret, "detail": "bad response"},
        ensure_ascii=True,
    ).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        message=message,
    )

    assert response_secret not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_deeply_nested_json_cannot_downgrade_known_http_failure():
    secret = "deep-json-secret"
    body = (
        "[" * 1_200
        + '{"token":"'
        + secret
        + '"}'
        + "]" * 1_200
    ).encode()

    outcome, _ = await _deliver(_FakeResponse(400, body=body))

    assert outcome.category == "payload"
    assert outcome.error_type == "bad_request"
    assert outcome.status_code == 400
    assert secret not in outcome.safe_summary
    assert len(outcome.safe_summary) <= 512
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_body_with_colliding_redaction_keys_is_omitted():
    target_secret = "target-key-value"
    body = json.dumps(
        {
            target_secret: "kept-a",
            "[REDACTED_KEY]": "kept-b",
        }
    ).encode()

    outcome, _ = await _deliver(
        _FakeResponse(400, body=body),
        target_id=target_secret,
    )

    assert target_secret not in outcome.safe_summary
    assert "kept-a" not in outcome.safe_summary
    assert "kept-b" not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


@pytest.mark.asyncio
async def test_sensitive_json_key_name_is_fully_redacted():
    key_secret = "token-response-side-secret"
    body = json.dumps({key_secret: "failure"}).encode()

    outcome, _ = await _deliver(_FakeResponse(400, body=body))

    assert key_secret not in outcome.safe_summary
    assert "response-side-secret" not in outcome.safe_summary
    assert "failure" not in outcome.safe_summary
    assert outcome.safe_summary == OMITTED_RESPONSE_SUMMARY


def _legacy_outcome(status_code: int | None, category: str):
    from core.outbound_transport import DeliveryOutcome

    return DeliveryOutcome(
        category=category,
        error_type="test",
        status_code=status_code,
        retry_after_seconds=None,
        duration_ms=1,
        safe_summary="",
        transport_phase=("response_received" if status_code is not None else "read"),
    )


@pytest.mark.parametrize(
    ("status_code", "category", "expected"),
    [
        (200, "success", True),
        (200, "endpoint", True),
        (201, "success", None),
        (204, "success", None),
        (400, "payload", False),
        (408, "transient", False),
        (425, "transient", False),
        (429, "transient", False),
        (499, "payload", False),
        (500, "transient", None),
        (503, "transient", None),
        (None, "ambiguous", None),
    ],
)
def test_legacy_adapter_preserves_exact_previous_status_semantics(
    status_code,
    category,
    expected,
):
    from core.outbound_transport import delivery_outcome_to_legacy

    assert delivery_outcome_to_legacy(
        _legacy_outcome(status_code, category)
    ) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "timeout_seconds",
    [0, -1, float("nan"), float("inf"), float("-inf"), "invalid"],
)
async def test_timeout_must_be_positive_and_finite(timeout_seconds):
    with pytest.raises(ValueError, match="timeout_seconds"):
        await _deliver(
            _FakeResponse(200),
            timeout_seconds=timeout_seconds,
        )


def test_optional_aiohttp_exception_classes_have_legacy_fallbacks():
    from core import outbound_transport

    optional_names = (
        "ConnectionTimeoutError",
        "SocketTimeoutError",
        "NonHttpUrlClientError",
    )
    originals = {
        name: getattr(aiohttp, name)
        for name in optional_names
        if hasattr(aiohttp, name)
    }
    try:
        for name in originals:
            delattr(aiohttp, name)
        reloaded = importlib.reload(outbound_transport)

        assert reloaded._classify_exception(
            TimeoutError("legacy timeout"),
            phase_hint="write",
        ) == ("ambiguous", "write", "write_timeout")
    finally:
        for name, value in originals.items():
            setattr(aiohttp, name, value)
        importlib.reload(outbound_transport)
