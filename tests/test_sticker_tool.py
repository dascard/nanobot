import asyncio
import json


def test_sticker_search_tool_returns_cq_send_code(db_session, monkeypatch):
    from core.sticker_memory import register_sticker
    from creatures.nanobot.prompts.skills.sticker_search.tool import StickerSearchTool

    register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/sticker.png",
        sticker_hash="tool-hash",
        description="震惊猫猫",
        tags=["震惊", "猫"],
        emotions=["surprised"],
    )

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.sticker_search.tool.SessionLocal",
        lambda: db_session,
    )

    result = asyncio.run(
        StickerSearchTool()._execute({"query": "震惊", "group_id": "123", "limit": 3})
    )

    assert not result.error
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["results"][0]["send_code"] == "[CQ:image,file=https://example.com/sticker.png]"
    assert "reply(content)" in payload["usage_hint"]
