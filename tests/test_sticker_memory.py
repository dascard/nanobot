import json
import os

from core.database import StickerMemory
from core.sticker_memory import (
    build_sticker_send_code,
    expand_sticker_refs_in_content,
    record_sticker_use,
    record_sticker_uses_in_content,
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


def test_search_stickers_global_pool_returns_all_groups(db_session):
    """全局池：查询返回所有群的 active sticker，不按群隔离。"""
    global_item = register_sticker(
        db_session,
        chat_stream_id="global",
        file_ref="https://example.com/global.png",
        sticker_hash="global-hash",
        description="震惊表情",
        status="active",
        tags=["震惊"],
        emotions=["surprised"],
    )
    group_item = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/group.png",
        sticker_hash="group-hash",
        description="群里常用的震惊猫",
        status="active",
        tags=["震惊", "猫"],
        emotions=["surprised"],
    )

    db_session.query(StickerMemory).filter(StickerMemory.id.in_([global_item["id"], group_item["id"]])).update({"preview_status": "ok"}, synchronize_session=False)
    db_session.commit()

    results = search_stickers(db_session, "震惊", limit=5)
    assert len(results) == 2
    ids = [item["id"] for item in results]
    assert global_item["id"] in ids
    assert group_item["id"] in ids

    row = db_session.query(StickerMemory).filter_by(id=group_item["id"]).one()
    row.status = "disabled"
    db_session.commit()

    filtered = search_stickers(db_session, "震惊", limit=5)
    assert all(item["id"] != group_item["id"] for item in filtered)


def test_scan_near_duplicates_dedupes_reverse_pair_without_autoflush(db_session):
    from core.database import StickerDuplicateCandidate
    from core.sticker_preview import scan_near_duplicates

    db_session.add_all([
        StickerMemory(
            chat_stream_id="qq:123:group",
            sticker_hash="near-a",
            file_ref="https://example.com/near-a.png",
            status="active",
            dedupe_status="unique",
            content_hash="hash-a",
            phash="0000000000000000",
            dhash="0000000000000000",
        ),
        StickerMemory(
            chat_stream_id="qq:123:group",
            sticker_hash="near-b",
            file_ref="https://example.com/near-b.png",
            status="active",
            dedupe_status="unique",
            content_hash="hash-b",
            phash="0000000000000000",
            dhash="0000000000000000",
        ),
    ])
    db_session.commit()

    result = scan_near_duplicates(db_session, limit=10)

    assert result["candidates_created"] == 1
    rows = db_session.query(StickerDuplicateCandidate).all()
    assert len(rows) == 1

    result = scan_near_duplicates(db_session, limit=10)

    assert result["candidates_created"] == 0
    assert db_session.query(StickerDuplicateCandidate).count() == 1


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


def test_record_sticker_uses_in_content_updates_matching_send_code(db_session):
    item = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/sent.png",
        sticker_hash="sent-hash",
        description="发送用表情",
    )

    count = record_sticker_uses_in_content(
        "[CQ:image,file=https://example.com/sent.png]",
        db=db_session,
    )

    assert count == 1
    row = db_session.query(StickerMemory).filter_by(id=item["id"]).one()
    assert row.usage_count == 1
    assert row.last_used is not None


def test_bare_chat_stream_id_normalizes_to_group_stream(db_session):
    register_sticker(
        db_session,
        chat_stream_id="123",
        file_ref="https://example.com/bare.png",
        sticker_hash="bare-hash",
        description="裸群号注册",
        status="active",
        tags=["裸群号"],
    )

    db_session.query(StickerMemory).filter_by(description="裸群号注册").update({"preview_status": "ok"}, synchronize_session=False)
    db_session.commit()

    results = search_stickers(db_session, "裸群号", limit=5)
    assert len(results) == 1
    assert results[0]["chat_stream_id"] == "qq:123:group"


def test_build_sticker_send_code_escapes_cq_sensitive_chars():
    assert build_sticker_send_code("https://example.com/a[1].png") == (
        "[CQ:image,file=https://example.com/a&#91;1&#93;.png]"
    )


def test_build_sticker_send_code_does_not_double_escape_html_entities(db_session):
    item = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/sticker.png?appid=1407&amp;fileid=abc&amp;rkey=def",
        sticker_hash="html-entity-hash",
        description="HTML 实体链接",
    )

    assert item["file_ref"] == "https://example.com/sticker.png?appid=1407&fileid=abc&rkey=def"
    assert "&amp;amp;" not in item["send_code"]
    assert item["send_code"].startswith("[CQ:image,file=")


def test_expand_sticker_refs_in_content_replaces_short_token(db_session):
    item = register_sticker(
        db_session,
        chat_stream_id="qq:123:group",
        file_ref="https://example.com/short-token.png",
        sticker_hash="short-token-hash",
        description="短 token 表情",
    )

    expanded = expand_sticker_refs_in_content(f"[sticker:{item['id']}]", db_session)

    assert expanded.startswith("[CQ:image,file=")


def test_expand_sticker_refs_prefers_public_cached_url(
    db_session,
    monkeypatch,
    tmp_path,
):
    import core.sticker_preview as sticker_preview

    cache_dir = str(tmp_path)
    monkeypatch.setattr(sticker_preview, "_cache_dir", lambda: cache_dir)

    local_path = os.path.join(cache_dir, "unit-public-sticker.png")
    with open(local_path, "wb") as f:
        f.write(b"fake-image")
    try:
        item = register_sticker(
            db_session,
            chat_stream_id="qq:123:group",
            file_ref="https://multimedia.nt.qq.com.cn/download?appid=1407&fileid=expired&rkey=bad",
            sticker_hash="public-cache-hash",
            description="本地缓存优先表情",
        )
        row = db_session.query(StickerMemory).filter_by(id=item["id"]).one()
        row.local_path = local_path
        row.preview_status = "ok"
        db_session.commit()
        monkeypatch.setenv("NANOBOT_PUBLIC_BASE_URL", "http://10.60.42.158:8000")

        expanded = expand_sticker_refs_in_content(f"[sticker:{item['id']}]", db_session)

        assert expanded == (
            f"[CQ:image,file=http://10.60.42.158:8000/api/v1/stickers/{item['id']}/image]"
        )
    finally:
        try:
            os.remove(local_path)
        except FileNotFoundError:
            pass
