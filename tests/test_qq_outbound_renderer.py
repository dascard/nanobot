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


def test_render_image_generated_image_id_uses_public_url(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.public_generated_image_url",
        lambda image_id: f"https://cdn.test/{image_id}.png",
    )

    result = render_qq_message_items(
        [{"type": "image", "generated_image_id": "img_12345678"}]
    )

    assert result.message == "[CQ:image,file=https://cdn.test/img_12345678.png]"
    assert result.warnings == []


def test_render_image_generated_image_id_without_public_url_keeps_token(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.public_generated_image_url",
        lambda image_id: None,
    )

    result = render_qq_message_items(
        [{"type": "image", "generated_image_id": "img_12345678"}]
    )

    assert result.message == "[generated_image:img_12345678]"
    assert "base64://" not in result.message
    assert result.warnings == ["generated_image_without_public_url:img_12345678"]


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


def test_render_rejects_model_supplied_cq_file_code():
    result = render_qq_outbound_envelope(
        {
            "reply": "[CQ:file,file=/srv/nanobot/private.txt]",
            "messages": [],
        }
    )

    assert result.message == "（文件消息已拒绝，请使用资产下载链接）"
    assert "/srv/nanobot" not in result.message


def test_render_expands_signed_asset_reply_token_at_outbound_boundary(monkeypatch):
    monkeypatch.setattr(
        "core.qq_outbound_renderer.expand_asset_download_refs_in_content",
        lambda content: content.replace(
            "[asset_download:signed.token]",
            "https://nanobot.test/api/v1/assets/hash/download?token=redacted",
        ),
    )

    result = render_qq_outbound_envelope(
        {"reply": "文件：[asset_download:signed.token]", "messages": []}
    )

    assert result.message == (
        "文件：https://nanobot.test/api/v1/assets/hash/download?token=redacted"
    )


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
