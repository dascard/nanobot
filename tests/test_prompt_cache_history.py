from __future__ import annotations

import json
from datetime import datetime

from core.db.models.chat import ChatLog, ConversationTurn


def test_private_current_event_becomes_identical_history_tail(db_session):
    from core.context_builder import build_chat_context
    from core.prompt_v2.context_adapters import build_current_user_event
    from core.prompt_v2.schema import PromptCompileRequest

    event_time = "2026-07-31 15:20:30 CST"
    metadata = {
        "bot_id": "bot-1",
        "bot_name": "小南",
        "current_message_id": "private-m1",
        "effort_constraint": "认真完成代码审查。",
        "event_time": event_time,
        "self_id": "bot-1",
        "sender_name": "甲",
        "session_name": "私聊",
        "timing_decision": "continue",
        "trigger_reason": "direct_call",
        "bot_aliases": ["南南", "小南"],
        "kind": "chat",
    }
    request = PromptCompileRequest(
        chat_type="private",
        session_id="private_cache",
        user_id="u1",
        sender_name="甲",
        session_name="私聊",
        trigger_reason="direct_call",
        timing_decision="continue",
        event_time=event_time,
        current_message_id="private-m1",
        self_id="bot-1",
        bot_id="bot-1",
        bot_name="小南",
        bot_aliases=["南南", "小南"],
        effort_constraint="认真完成代码审查。",
        user_input="检查这个缓存问题",
    )
    expected_user = build_current_user_event(request)
    created_at = datetime(2026, 7, 31, 15, 20, 30)
    db_session.add_all(
        [
            ConversationTurn(
                user_id="u1",
                session_id="private_cache",
                role="user",
                content="检查这个缓存问题",
                created_at=created_at,
                source_message_ids_json='["private-m1"]',
                meta_json=json.dumps(metadata, ensure_ascii=False),
            ),
            ConversationTurn(
                user_id="u1",
                session_id="private_cache",
                role="assistant",
                content="已定位缓存前缀中的动态字段。",
                created_at=created_at,
                meta_json='{"kind":"chat"}',
            ),
        ]
    )
    db_session.commit()

    _header, messages, _debug = build_chat_context(
        db_session,
        "private_cache",
        user_id="u1",
        is_group=False,
        max_total=10000,
    )

    assert messages[-2:] == [
        {
            "role": "user",
            "content": expected_user,
            "meta_json": json.dumps(metadata, ensure_ascii=False),
            "_created_at": created_at,
            "turn_id": messages[-2]["turn_id"],
        },
        {
            "role": "assistant",
            "content": "已定位缓存前缀中的动态字段。",
            "meta_json": '{"kind":"chat"}',
            "_created_at": created_at,
            "turn_id": messages[-1]["turn_id"],
        },
    ]


def test_group_pending_event_becomes_identical_history_tail(db_session):
    from core.context_builder import build_group_recent_messages
    from core.group_runtime.state import GroupPendingMessage, _pending_payload
    from core.prompt_v2.context_adapters import ensure_user_input_block

    created_at = datetime(2026, 7, 31, 16, 0, 0)
    first_meta = {
        "kind": "chat",
        "directed": {
            "at_bot": True,
            "reply_to_bot": False,
            "at_others": True,
            "reply_to_others": False,
        },
        "mentions": [
            {"user_id": "u2", "nickname": "乙", "is_bot": False},
        ],
        "reply_to": {"sender_name": "丙", "content": "上一条原话"},
    }
    second_meta = {
        "kind": "chat",
        "directed": {
            "at_bot": False,
            "reply_to_bot": True,
            "at_others": False,
            "reply_to_others": False,
        },
        "mentions": [],
        "reply_to": None,
    }
    rows = [
        ChatLog(
            user_id="group_cache",
            session_id="group_cache",
            role="ambient",
            sender_name="甲",
            content="[甲]: 第一条",
            message_id="group-m1",
            created_at=created_at,
            processed=1,
            meta_json=json.dumps(first_meta, ensure_ascii=False),
        ),
        ChatLog(
            user_id="group_cache",
            session_id="group_cache",
            role="ambient",
            sender_name="乙",
            content="[乙]: 第二条",
            message_id="group-m2",
            created_at=created_at,
            processed=1,
            meta_json=json.dumps(second_meta, ensure_ascii=False),
        ),
        ChatLog(
            user_id="group_cache",
            session_id="group_cache",
            role="assistant",
            sender_name="小南",
            content="我已经看完这两条。",
            message_id="group-m2",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"group_reply"}',
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    current = _pending_payload(
        [
            GroupPendingMessage(
                sender_id="u1",
                sender_name="甲",
                message="第一条",
                message_id="group-m1",
                ts=created_at.timestamp(),
                directed=first_meta["directed"],
                mentions=first_meta["mentions"],
                reply_to=first_meta["reply_to"],
            ),
            GroupPendingMessage(
                sender_id="u2",
                sender_name="乙",
                message="第二条",
                message_id="group-m2",
                ts=created_at.timestamp(),
                directed=second_meta["directed"],
                mentions=[],
            ),
        ]
    )["pending_text"]

    messages, _debug = build_group_recent_messages(
        db_session,
        "group_cache",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == ensure_user_input_block(current)
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "我已经看完这两条。"
