from core.tracing import _json_dumps, _redact


def test_redact_authorization_header():
    text = _json_dumps({
        "Authorization": "Bearer abc123",
        "Content-Type": "application/json",
    }, max_chars=10000)
    assert "Bearer abc123" not in text
    assert "abc123" not in text
    assert "REDACTED" in text or "application/json" in text


def test_redact_api_key():
    text = _json_dumps({"api_key": "sk-secret-key"}, max_chars=10000)
    assert "sk-secret-key" not in text


def test_request_json_preserves_messages():
    messages = [{"role": "user", "content": "hello"}]
    payload = {"model": "gpt-4", "messages": messages, "temperature": 0.7}
    text = _json_dumps(payload, max_chars=200000)
    assert "gpt-4" in text
    assert "hello" in text
    assert "temperature" in text
