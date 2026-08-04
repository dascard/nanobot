import json

from core.database import StickerMemory


def test_safe_parse_sticker_summary_accepts_normal_json():
    from core.sticker_memory import _safe_parse_sticker_summary

    parsed = _safe_parse_sticker_summary(json.dumps({
        "image_count": 1,
        "overall_summary": "猫猫震惊看着屏幕",
        "per_image": [{"index": 1, "summary": "猫猫震惊看着屏幕"}],
        "keywords": ["猫", "震惊"],
    }, ensure_ascii=False))

    assert parsed["overall_summary"] == "猫猫震惊看着屏幕"
    assert parsed["per_image"][0]["summary"] == "猫猫震惊看着屏幕"
    assert parsed.get("_parse_fallback") is not True


def test_safe_parse_sticker_summary_accepts_code_fenced_json():
    from core.sticker_memory import _safe_parse_sticker_summary

    parsed = _safe_parse_sticker_summary(
        "```json\n"
        '{"overall_summary":"熊猫头表示不理解","per_image":[{"index":1,"summary":"熊猫头表示不理解"}]}\n'
        "```"
    )

    assert parsed["overall_summary"] == "熊猫头表示不理解"
    assert parsed["per_image"][0]["summary"] == "熊猫头表示不理解"


def test_safe_parse_sticker_summary_falls_back_to_overall_summary_in_broken_json():
    from core.sticker_memory import _safe_parse_sticker_summary

    parsed = _safe_parse_sticker_summary(
        '{"image_count":1,"overall_summary":"小人拍桌说\\"这也太离谱了",'
    )

    assert parsed["overall_summary"] == '小人拍桌说"这也太离谱了'
    assert parsed["per_image"][0]["summary"] == '小人拍桌说"这也太离谱了'
    assert parsed["_parse_fallback"] is True
    assert parsed["confidence"] == "low"


def test_safe_parse_sticker_summary_falls_back_to_raw_text():
    from core.sticker_memory import _safe_parse_sticker_summary

    parsed = _safe_parse_sticker_summary("一个捂脸猫猫表情，表达无奈和好笑。后面还有模型多余解释。")

    assert parsed["overall_summary"] == "一个捂脸猫猫表情，表达无奈和好笑。后面还有模型多余解释。"
    assert parsed["per_image"][0]["summary"] == parsed["overall_summary"]
    assert parsed["_parse_fallback"] is True


def test_describe_sticker_with_qwen_uses_raw_text_fallback_and_tags(monkeypatch):
    from core.sticker_memory import describe_sticker_with_qwen
    from core.llm_trace_context import get_llm_trace_vars
    import core.media_tool_runtime as media_runtime

    seen_sources = []

    class FakeSummaryProvider:
        def summarize(self, files, focus):
            assert files == ("https://example.com/cat.png",)
            assert "聊天表情包" in focus
            seen_sources.append(get_llm_trace_vars()[2])
            return "猫猫疑惑问号脸，适合表示不理解。"

    monkeypatch.setattr(
        media_runtime,
        "get_image_summary_provider",
        lambda: FakeSummaryProvider(),
    )

    payload = describe_sticker_with_qwen("https://example.com/cat.png")

    assert payload["description"] == "猫猫疑惑问号脸，适合表示不理解。"
    assert payload["tags"]
    assert any("猫猫疑惑" in tag for tag in payload["tags"])
    assert payload["raw_summary"]["_raw_text"] == "猫猫疑惑问号脸，适合表示不理解。"
    assert seen_sources == ["image_summary.sticker_auto_describe"]


def test_auto_describe_sticker_does_not_mark_ok_when_description_empty(db_session, monkeypatch):
    import core.sticker_memory as sticker_memory

    row = StickerMemory(
        chat_stream_id="qq:123:group",
        sticker_hash="empty-description",
        file_ref="https://example.com/empty.png",
        describe_status="pending",
    )
    db_session.add(row)
    db_session.commit()
    row_id = row.id

    monkeypatch.setattr(sticker_memory, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(sticker_memory, "_sticker_image_ref_for_describe", lambda item: item.file_ref)
    monkeypatch.setattr(
        sticker_memory,
        "describe_sticker_with_qwen",
        lambda ref: {"description": "", "tags": ["空"], "raw_summary": {"overall_summary": ""}},
    )

    sticker_memory.auto_describe_sticker(row_id, force=True)

    updated = db_session.query(StickerMemory).filter_by(id=row_id).one()
    assert updated.describe_status == "failed"
    assert updated.describe_attempts == 1
    assert updated.describe_last_error == "empty description after parse"
