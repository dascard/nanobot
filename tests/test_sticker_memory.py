import json

from core.database import StickerMemory
from core.sticker_memory import (
    build_sticker_send_code,
    record_sticker_use,
    register_sticker,
    search_stickers,
)


def test_register_sticker_upserts_by_stream_and_hash(db_session):
    first = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/a.png",
        sticker_hash="hash-a",
        description="猫猫震惊",
        tags=["猫", "震惊"],
        emotions=["surprised"],
        source_type="auto",
    )
    second = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/a.png",
        sticker_hash="hash-a",
        description="猫猫震惊升级版",
        tags=["猫", "震惊", "升级"],
        emotions=["surprised"],
        source_type="auto",
    )

    rows = db_session.query(StickerMemory).all()
    assert len(rows) == 1
    assert first["id"] == second["id"]
    assert rows[0].description == "猫猫震惊升级版"
    assert json.loads(rows[0].tags_json) == ["猫", "震惊", "升级"]
    assert rows[0].source_count == 2


def test_search_stickers_prefers_group_over_global_and_filters_disabled(db_session):
    global_item = register_sticker(
        db_session,
        chat_stream_id="global",
        file_ref="https://example.com/global.png",
        sticker_hash="global-hash",
        description="震惊表情",
        tags=["震惊"],
        emotions=["surprised"],
    )
    group_item = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/group.png",
        sticker_hash="group-hash",
        description="群里常用的震惊猫",
        tags=["震惊", "猫"],
        emotions=["surprised"],
    )

    results = search_stickers(db_session, "震惊", chat_stream_id="qq:123:group", limit=5)
    assert [item["id"] for item in results[:2]] == [group_item["id"], global_item["id"]]
    assert results[0]["send_code"] == "[CQ:image,file=https://example.com/group.png]"

    row = db_session.query(StickerMemory).filter_by(id=group_item["id"]).one()
    row.status = "disabled"
    db_session.commit()

    filtered = search_stickers(db_session, "震惊", chat_stream_id="qq:123:group", limit=5)
    assert all(item["id"] != group_item["id"] for item in filtered)


def test_record_sticker_use_updates_counter_and_last_used(db_session):
    item = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/use.png",
        sticker_hash="use-hash",
        description="拍桌",
    )

    updated = record_sticker_use(db_session, item["id"])

    assert updated["usage_count"] == 1
    assert updated["last_used"] is not None


def test_build_sticker_send_code_escapes_cq_sensitive_chars():
    assert build_sticker_send_code("https://example.com/a[1].png") == (
        "[CQ:image,file=https://example.com/a&#91;1&#93;.png]"
    )
