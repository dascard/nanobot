from core.qq_outbound_renderer import render_qq_message_items
from core.qq_outbound_renderer import render_qq_outbound_envelope


def test_render_text_and_html_in_order():
    result = render_qq_message_items(
        [
            {"type": "text", "text": "A"},
            {"type": "html", "text": "<article>B</article>"},
        ]
    )

    assert result.message == "A\n<article>B</article>"
    assert result.messages == [
        {"type": "text", "text": "A"},
        {"type": "html", "text": "<article>B</article>"},
    ]


def test_render_falls_back_to_reply_when_messages_empty():
    result = render_qq_outbound_envelope({"reply": "你好", "messages": []})

    assert result.message == "你好"
    assert result.messages == [{"type": "text", "text": "你好"}]


def test_render_image_url_as_cq_image():
    result = render_qq_message_items(
        [{"type": "image", "url": "https://example.test/a.png"}]
    )

    assert result.message == "[CQ:image,file=https://example.test/a.png]"


def test_render_text_expands_sticker_token(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.expand_sticker_refs_in_content",
        lambda content: content.replace(
            "[sticker:42]",
            "[CQ:image,file=https://sticker.test/42.png]",
        ),
    )

    result = render_qq_outbound_envelope(
        {"reply": "贴纸：[sticker:42]", "messages": []}
    )

    assert result.message == "贴纸：[CQ:image,file=https://sticker.test/42.png]"


def test_render_direct_cq_code_as_legacy_content():
    result = render_qq_outbound_envelope(
        {
            "reply": "[CQ:image,file=https://example.test/already.png]",
            "messages": [],
        }
    )

    assert result.message == "[CQ:image,file=https://example.test/already.png]"


def test_render_generated_image_token_uses_public_url(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.public_generated_image_url",
        lambda image_id: f"https://cdn.test/{image_id}.png",
    )

    result = render_qq_outbound_envelope(
        {"reply": "图：[generated_image:img_1]", "messages": []}
    )

    assert result.message == "图：[CQ:image,file=https://cdn.test/img_1.png]"
    assert result.warnings == []


def test_render_generated_image_without_public_url_keeps_token(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.public_generated_image_url",
        lambda image_id: None,
    )

    result = render_qq_outbound_envelope(
        {"reply": "[generated_image:img_1]", "messages": []}
    )

    assert result.message == "[generated_image:img_1]"
    assert "base64://" not in result.message
    assert result.warnings == ["generated_image_without_public_url:img_1"]


def test_render_keeps_reply_meta_without_deriving_cq_at():
    result = render_qq_outbound_envelope(
        {
            "reply": "你好",
            "messages": [],
            "reply_meta": {
                "send_mode": "quote",
                "mentions": ["10001"],
                "at_sender": True,
                "_agent_result": "drop",
            },
        }
    )

    assert result.message == "你好"
    assert result.reply_meta == {
        "send_mode": "quote",
        "mentions": ["10001"],
        "at_sender": True,
    }
    assert "[CQ:at" not in result.message


def test_render_empty_envelope_returns_empty_message():
    result = render_qq_outbound_envelope(None)

    assert result.message == ""
    assert result.messages == []
