import inspect

from tests.async_helpers import run_async


def test_push_envelope_to_qq_keeps_legacy_push_signature():
    from core import daily_digest

    assert list(inspect.signature(daily_digest.push_to_qq).parameters) == [
        "target_type",
        "target_id",
        "message",
    ]


def test_push_envelope_to_qq_derives_message_from_reply(monkeypatch):
    from core import daily_digest

    calls = []

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)

    result = run_async(
        daily_digest.push_envelope_to_qq(
            "private",
            "u1",
            {"reply": "推送正文", "messages": [{"type": "text", "text": "忽略"}]},
        )
    )

    assert result is True
    assert calls == [("private", "u1", "推送正文")]


def test_push_envelope_to_qq_falls_back_to_textual_messages(monkeypatch):
    from core import daily_digest

    calls = []

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)

    result = run_async(
        daily_digest.push_envelope_to_qq(
            "group",
            "g1",
            {
                "reply": "",
                "messages": [
                    {"type": "text", "text": "A"},
                    {"type": "html", "text": "<article>B</article>"},
                    {"type": "image", "url": "https://example.com/a.png"},
                ],
            },
        )
    )

    assert result is True
    assert calls == [("group", "g1", "A\n<article>B</article>")]


def test_push_envelope_to_qq_skips_empty_message(monkeypatch):
    from core import daily_digest

    calls = []

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)

    result = run_async(daily_digest.push_envelope_to_qq("group", "g1", {"messages": []}))

    assert result is False
    assert calls == []
