import inspect
import logging

from tests.async_helpers import run_async


def test_push_envelope_to_qq_keeps_legacy_push_signature():
    from core import daily_digest

    assert list(inspect.signature(daily_digest.push_to_qq).parameters) == [
        "target_type",
        "target_id",
        "message",
    ]


def test_push_envelope_to_qq_renders_structured_messages(monkeypatch):
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
    assert calls == [("private", "u1", "忽略")]


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
    assert calls == [
        (
            "group",
            "g1",
            "A\n<article>B</article>\n[CQ:image,file=https://example.com/a.png]",
        )
    ]


def test_push_envelope_to_qq_renders_generated_image_public_url(monkeypatch):
    from core import daily_digest
    from core import qq_outbound_renderer

    calls = []
    render_calls = []
    original_render = qq_outbound_renderer.render_qq_outbound_envelope

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    def spy_render(envelope, *, allow_base64=False):
        render_calls.append(allow_base64)
        return original_render(envelope, allow_base64=allow_base64)

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)
    monkeypatch.setattr(
        qq_outbound_renderer,
        "render_qq_outbound_envelope",
        spy_render,
    )
    monkeypatch.setattr(
        qq_outbound_renderer,
        "public_generated_image_url",
        lambda image_id: f"https://cdn.test/{image_id}.png",
    )

    result = run_async(
        daily_digest.push_envelope_to_qq(
            "group",
            "123",
            {
                "reply": "[generated_image:img_1]",
                "messages": [{"type": "text", "text": "[generated_image:img_1]"}],
                "reply_meta": {},
            },
        )
    )

    assert result is True
    assert render_calls == [False]
    assert calls == [("group", "123", "[CQ:image,file=https://cdn.test/img_1.png]")]


def test_push_envelope_to_qq_keeps_generated_image_token_without_base64(
    monkeypatch, caplog
):
    from core import daily_digest
    from core import qq_outbound_renderer

    calls = []

    async def fake_push(target_type, target_id, message):
        calls.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(daily_digest, "push_to_qq", fake_push)
    monkeypatch.setattr(
        qq_outbound_renderer,
        "public_generated_image_url",
        lambda image_id: None,
    )
    caplog.set_level(logging.WARNING, logger="nanobot.daily_digest")

    result = run_async(
        daily_digest.push_envelope_to_qq(
            "group",
            "123",
            {"reply": "[generated_image:img_1]", "messages": []},
        )
    )

    assert result is True
    assert calls == [("group", "123", "[generated_image:img_1]")]
    assert "base64://" not in calls[0][2]
    assert "generated_image_without_public_url:img_1" in caplog.text


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
