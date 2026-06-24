import json
import logging
from datetime import datetime

from app.memory_digest.builder import MemoryDigestBuilder
from core.database import ChatLog


def _db_time(year: int, month: int, day: int, hour: int, minute: int, second: int) -> datetime:
    # SQLite ORM DateTime fixture 保持 naive 本地墙钟时间语义。
    return datetime(year, month, day, hour, minute, second)  # noqa: DTZ001


def _log(*, content: str, sender_name: str = "Alice", log_id: int = 1) -> ChatLog:
    return ChatLog(
        id=log_id,
        user_id="group_42",
        session_id="group_42",
        sender_name=sender_name,
        role="ambient",
        content=content,
        processed=1,
        created_at=_db_time(2026, 5, 28, 12, 0, 0),
        meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
    )


def test_memory_digest_builder_logs_invalid_meta_without_leaking_raw_meta(caplog):
    bad_meta = "{bad json digest-secret}"
    log = _log(content="[Alice]: 讨论 memory digest 调试日志", log_id=9)
    log.meta_json = bad_meta

    with caplog.at_level(logging.DEBUG, logger="nanobot.memory_digest.builder"):
        result = MemoryDigestBuilder().build(
            user_id="group_42",
            session_id="group_42",
            digest_date="2026-05-28",
            logs=[log],
        )

    assert result.status in {"active", "skipped"}
    assert "invalid meta_json" in caplog.text
    assert "digest-secret" not in caplog.text
    assert str(len(bad_meta)) in caplog.text


def test_memory_digest_builder_filters_sender_prefixed_image_and_command_noise():
    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-28",
        logs=[
            _log(content="[Alice]: [图片:1张]", sender_name="Alice", log_id=1),
            _log(content="[Bob]: 签到", sender_name="Bob", log_id=2),
        ],
    )

    assert result.status == "skipped"
    assert result.meta["source_stats"]["filtered_by_reason"] == {
        "image_placeholder": 1,
        "bot_command": 1,
    }


def test_memory_digest_builder_uses_message_body_for_keywords_and_details():
    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-28",
        logs=[
            _log(content="[Alice]: 讨论摘要浏览默认展示", sender_name="Alice", log_id=1),
            _log(content="[Alice]: 摘要浏览需要显示预览", sender_name="Alice", log_id=2),
            _log(content="[Bob]: 长期摘要默认不能只显示横杠", sender_name="Bob", log_id=3),
        ],
    )

    assert result.status == "active"
    keywords = result.meta["preview"]["keywords"]
    assert "Alice" not in keywords
    assert "Bob" not in keywords
    details = result.meta["long_summary"]["important_details"]
    assert "Alice 提到：讨论摘要浏览默认展示" in details
    assert all("[Alice]:" not in item and "[Bob]:" not in item for item in details)
    assert "代表消息" not in result.meta["recall_cards"][0]["text"]


def test_memory_digest_builder_does_not_promote_urls_to_topics_or_cards():
    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-28",
        logs=[
            _log(
                content="[Alice]: https://www.bilibili.com/video/BV1vMXDBeECd?spm_id_from=333.999",
                sender_name="Alice",
                log_id=1,
            ),
            _log(
                content="[Alice]: 【英语阅读刻板印象】 https://www.bilibili.com/video/BV1vMXDBeECd",
                sender_name="Alice",
                log_id=2,
            ),
            _log(content="[Bob]: 我真求了，今天才做过这个阅读题", sender_name="Bob", log_id=3),
        ],
    )

    assert result.status == "active"
    assert result.meta["source_stats"]["filtered_by_reason"]["url_only"] == 1
    keywords = result.meta["preview"]["keywords"]
    assert "https" not in keywords
    assert "www.bilibili.com" not in keywords
    assert "BV1vMXDBeECd" not in keywords
    assert "英语阅读刻板印象" in keywords
    combined = json.dumps(result.meta["long_summary"], ensure_ascii=False)
    cards = json.dumps(result.meta["recall_cards"], ensure_ascii=False)
    assert "http" not in combined
    assert "bilibili.com" not in cards


def test_memory_digest_builder_uses_high_signal_details_for_recall_cards():
    result = MemoryDigestBuilder().build(
        user_id="group_42",
        session_id="group_42",
        digest_date="2026-05-28",
        logs=[
            _log(content="[Alice]: 999听到鸟叫了", sender_name="Alice", log_id=1),
            _log(content="[Bob]: PCL这是什么原理", sender_name="Bob", log_id=2),
            _log(content="[Carol]: 好像是申请一大堆内存然后触发系统转储虚拟内存吧", sender_name="Carol", log_id=3),
            _log(content="[Dave]: 好像就是增大 pagefile 交换", sender_name="Dave", log_id=4),
        ],
    )

    assert result.status == "active"
    cards = result.meta["recall_cards"]
    card_text = "\n".join(card["text"] for card in cards)
    assert len(cards) >= 2
    assert "PCL这是什么原理" in card_text
    assert "虚拟内存" in card_text
    assert "999听到鸟叫了" not in cards[0]["text"]
