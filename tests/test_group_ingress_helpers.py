import logging


def test_safe_meta_logs_invalid_json_without_leaking_raw_meta(caplog):
    from app.group_ingress.helpers import safe_meta

    raw = "{bad json secret-token}"

    with caplog.at_level(logging.DEBUG, logger="nanobot.group_ingress"):
        result = safe_meta(raw)

    assert result == {}
    assert "invalid meta_json" in caplog.text
    assert "secret-token" not in caplog.text
    assert str(len(raw)) in caplog.text


def test_get_group_talk_value_logs_fallback(monkeypatch, caplog):
    from app.group_ingress.helpers import get_group_talk_value

    def broken_get_stream_config(*_args, **_kwargs):
        raise RuntimeError("config boom")

    monkeypatch.setattr("core.expression_memory.get_stream_config", broken_get_stream_config)

    with caplog.at_level(logging.DEBUG, logger="nanobot.group_ingress"):
        value = get_group_talk_value("group_123")

    assert value == 0.5
    assert "talk_value fallback" in caplog.text
    assert "group_123" in caplog.text
    assert "config boom" in caplog.text
