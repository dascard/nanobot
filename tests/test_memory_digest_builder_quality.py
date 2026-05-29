import json
from datetime import datetime

from app.memory_digest.builder import MemoryDigestBuilder
from core.database import ChatLog


def _log(*, content: str, sender_name: str = "Alice", log_id: int = 1) -> ChatLog:
    return ChatLog(
        id=log_id,
        user_id="group_42",
        session_id="group_42",
        sender_name=sender_name,
        role="ambient",
        content=content,
        processed=1,
        created_at=datetime(2026, 5, 28, 12, 0, 0),
        meta_json=json.dumps({"kind": "chat"}, ensure_ascii=False),
    )


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
    assert details[0] == "Alice 提到：讨论摘要浏览默认展示"
    assert "代表消息" not in result.meta["recall_cards"][0]["text"]
