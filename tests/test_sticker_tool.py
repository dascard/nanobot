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
    assert payload["results"][0]["reply_token"] == f"[sticker:{payload['results'][0]['id']}]"
    assert payload["results"][0]["send_code"] == "[CQ:image,file=https://example.com/sticker.png]"
    assert "reply_token" in payload["usage_hint"]


def test_reply_tool_records_sent_sticker_usage(db_session, monkeypatch):
    from core.database import StickerMemory
    from core.sticker_memory import register_sticker
    from creatures.nanobot.prompts.skills.reply.tool import ReplyTool

    sticker = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/reply.png",
        sticker_hash="reply-hash",
        description="回复发送表情",
    )

    monkeypatch.setattr("core.sticker_memory.SessionLocal", lambda: db_session)

    result = asyncio.run(
        ReplyTool()._execute({"content": "[CQ:image,file=https://example.com/reply.png]"})
    )

    assert not result.error
    row = db_session.query(StickerMemory).filter_by(id=sticker["id"]).one()
    assert row.usage_count == 1
    assert row.last_used is not None


def test_reply_tool_expands_sticker_token_and_records_usage(db_session, monkeypatch):
    from core.database import StickerMemory
    from core.sticker_memory import register_sticker
    from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER, ReplyTool

    sticker = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/token.png?x=1&y=2",
        sticker_hash="reply-token-hash",
        description="token 发送表情",
    )

    monkeypatch.setattr("core.sticker_memory.SessionLocal", lambda: db_session)

    result = asyncio.run(ReplyTool()._execute({"content": f"[sticker:{sticker['id']}]"}))

    assert not result.error
    payload = json.loads(result.output)
    assert payload[REPLY_MARKER]["content"] == (
        "[CQ:image,file=https://example.com/token.png?x=1&amp;y=2]"
    )
    row = db_session.query(StickerMemory).filter_by(id=sticker["id"]).one()
    assert row.usage_count == 1
    assert row.last_used is not None


def test_reply_tool_canonicalizes_legacy_double_escaped_sticker_code(db_session, monkeypatch):
    from core.database import StickerMemory
    from core.sticker_memory import register_sticker
    from creatures.nanobot.prompts.skills.reply.tool import REPLY_MARKER, ReplyTool

    sticker = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/legacy.png?x=1&y=2",
        sticker_hash="legacy-double-escaped-hash",
        description="旧库二次转义表情",
    )
    row = db_session.query(StickerMemory).filter_by(id=sticker["id"]).one()
    row.send_code = "[CQ:image,file=https://example.com/legacy.png?x=1&amp;amp;y=2]"
    db_session.commit()

    monkeypatch.setattr("core.sticker_memory.SessionLocal", lambda: db_session)

    result = asyncio.run(ReplyTool()._execute({"content": f"[sticker:{sticker['id']}]"}))

    payload = json.loads(result.output)
    assert payload[REPLY_MARKER]["content"] == (
        "[CQ:image,file=https://example.com/legacy.png?x=1&amp;y=2]"
    )
    row = db_session.query(StickerMemory).filter_by(id=sticker["id"]).one()
    assert row.usage_count == 1
