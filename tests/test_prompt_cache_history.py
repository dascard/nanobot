from __future__ import annotations

import json
from datetime import datetime

import pytest

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


def test_private_truncated_current_event_becomes_identical_history_tail(db_session):
    from api.chat_content_helpers import (
        build_conversation_user_content,
        build_multimodal_user_input_text,
    )
    from core.context_builder import build_chat_context
    from core.prompt_v2.context_adapters import build_current_user_event
    from core.prompt_v2.schema import PromptCompileRequest

    raw_query = "超长私聊消息" * 500
    event_time = "2026-08-13 19:20:30 CST"
    metadata = {
        "current_message_id": "private-long-m1",
        "event_time": event_time,
        "sender_name": "甲",
        "session_name": "私聊",
        "kind": "chat",
    }
    current_content = build_multimodal_user_input_text(
        raw_query,
        None,
        max_chars=2000,
    )
    persisted_content = build_conversation_user_content(raw_query, None)
    assert persisted_content == current_content
    assert 2000 < len(persisted_content) <= 2200

    request = PromptCompileRequest(
        chat_type="private",
        session_id="private_long_cache",
        user_id="u1",
        sender_name="甲",
        session_name="私聊",
        event_time=event_time,
        current_message_id="private-long-m1",
        user_input=current_content,
    )
    expected_user = build_current_user_event(request)
    created_at = datetime(2026, 8, 13, 19, 20, 30)
    db_session.add(ConversationTurn(
        user_id="u1",
        session_id="private_long_cache",
        role="user",
        content=persisted_content,
        created_at=created_at,
        source_message_ids_json='["private-long-m1"]',
        meta_json=json.dumps(metadata, ensure_ascii=False),
    ))
    db_session.commit()

    _header, messages, _debug = build_chat_context(
        db_session,
        "private_long_cache",
        user_id="u1",
        is_group=False,
        max_total=10000,
    )

    assert messages[-1]["content"] == expected_user
    assert "[长消息摘要]" not in messages[-1]["content"]


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
            source_message_ids_json='["group-m1", "group-m2"]',
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


def test_group_oversized_pending_event_becomes_identical_history_tail(db_session):
    from core.context_builder import build_group_recent_messages
    from core.group_runtime.state import GroupPendingMessage, _pending_payload
    from core.prompt_v2.context_adapters import ensure_user_input_block

    created_at = datetime(2026, 8, 13, 16, 5, 0)
    content = "超长群消息" * 3000
    db_session.add_all([
        ChatLog(
            user_id="group_oversized_cache",
            session_id="group_oversized_cache",
            role="ambient",
            sender_name="超长用户",
            content=f"[超长用户]: {content}",
            message_id="oversized-m1",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ),
        ChatLog(
            user_id="group_oversized_cache",
            session_id="group_oversized_cache",
            role="assistant",
            sender_name="小南",
            content="收到。",
            message_id="oversized-m1",
            source_message_ids_json='["oversized-m1"]',
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"group_reply"}',
        ),
    ])
    db_session.commit()
    current = _pending_payload([
        GroupPendingMessage(
            sender_id="u1",
            sender_name="超长用户",
            message=content,
            message_id="oversized-m1",
            ts=created_at.timestamp(),
        ),
    ])["pending_text"]

    messages, _debug = build_group_recent_messages(
        db_session,
        "group_oversized_cache",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    assert messages[0]["content"] == ensure_user_input_block(current)


def test_group_message_that_looks_like_archive_prefix_keeps_identical_history_tail(
    db_session,
):
    from core.context_builder import build_group_recent_messages
    from core.group_runtime.state import GroupPendingMessage, _pending_payload
    from core.prompt_v2.context_adapters import ensure_user_input_block

    created_at = datetime(2026, 8, 13, 16, 7, 0)
    raw_content = "[甲]: 这是用户原文，不是归档包装"
    db_session.add_all([
        ChatLog(
            user_id="group_prefix_like_cache",
            session_id="group_prefix_like_cache",
            role="ambient",
            sender_name="甲",
            content=f"[甲]: {raw_content}",
            message_id="prefix-like-m1",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ),
        ChatLog(
            user_id="group_prefix_like_cache",
            session_id="group_prefix_like_cache",
            role="assistant",
            sender_name="小南",
            content="[小南]: 这段回复也应原样保留。",
            message_id="prefix-like-m1",
            source_message_ids_json='["prefix-like-m1"]',
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"group_reply"}',
        ),
    ])
    db_session.commit()
    current = _pending_payload([
        GroupPendingMessage(
            sender_id="u1",
            sender_name="甲",
            message=raw_content,
            message_id="prefix-like-m1",
            ts=created_at.timestamp(),
        ),
    ])["pending_text"]

    messages, _debug = build_group_recent_messages(
        db_session,
        "group_prefix_like_cache",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    assert messages[0]["content"] == ensure_user_input_block(current)
    assert "[发言内容][甲]: 这是用户原文" in messages[0]["content"]
    assert messages[1]["content"] == "[小南]: 这段回复也应原样保留。"


def test_group_reply_keeps_causal_prefix_when_new_message_arrives_during_generation(
    db_session,
):
    from core.context_builder import build_group_recent_messages
    from core.group_runtime.state import GroupPendingMessage, _pending_payload
    from core.prompt_v2.context_adapters import ensure_user_input_block

    created_at = datetime(2026, 8, 13, 16, 10, 0)
    for index in range(1, 4):
        db_session.add(ChatLog(
            user_id="group_causal_cache",
            session_id="group_causal_cache",
            role="ambient",
            sender_name=f"群友{index}",
            content=f"[群友{index}]: 第{index}条消息",
            message_id=f"causal-{index}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ))
    db_session.add(ChatLog(
        user_id="group_causal_cache",
        session_id="group_causal_cache",
        role="assistant",
        sender_name="小南",
        content="这是对前两条消息的回复。",
        message_id="causal-2",
        source_message_ids_json='["causal-1", "causal-2"]',
        created_at=created_at,
        processed=1,
        meta_json='{"kind":"group_reply"}',
    ))
    db_session.commit()
    previous_current = _pending_payload([
        GroupPendingMessage(
            sender_id="u1",
            sender_name="群友1",
            message="第1条消息",
            message_id="causal-1",
            ts=created_at.timestamp(),
        ),
        GroupPendingMessage(
            sender_id="u2",
            sender_name="群友2",
            message="第2条消息",
            message_id="causal-2",
            ts=created_at.timestamp(),
        ),
    ])["pending_text"]

    messages, _debug = build_group_recent_messages(
        db_session,
        "group_causal_cache",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    assert [message["role"] for message in messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert messages[0]["content"] == ensure_user_input_block(previous_current)
    assert messages[1]["content"] == "这是对前两条消息的回复。"
    assert messages[2]["message_id"] == "causal-3"


def test_group_unanswered_ambient_messages_keep_append_only_prefix(db_session):
    from core.context_builder import build_group_recent_messages

    created_at = datetime(2026, 8, 13, 16, 20, 0)

    def add_message(index: int) -> None:
        db_session.add(ChatLog(
            user_id="group_append_only",
            session_id="group_append_only",
            role="ambient",
            sender_name=f"群友{index}",
            content=f"第{index}条未回复消息",
            message_id=f"append-{index}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ))
        db_session.commit()

    add_message(1)
    add_message(2)
    before, _ = build_group_recent_messages(
        db_session,
        "group_append_only",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    add_message(3)
    after, _ = build_group_recent_messages(
        db_session,
        "group_append_only",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    assert len(before) == 2
    assert len(after) == 3
    assert after[:len(before)] == before


def test_group_no_reply_batch_becomes_identical_history_tail(db_session):
    from app.group_ingress.helpers import record_group_prompt_batch
    from core.context_builder import build_group_recent_messages
    from core.group_runtime.state import GroupPendingMessage, _pending_payload
    from core.prompt_v2.context_adapters import ensure_user_input_block

    created_at = datetime(2026, 8, 13, 16, 25, 0)
    for index in range(1, 3):
        db_session.add(ChatLog(
            user_id="group_no_reply_batch",
            session_id="group_no_reply_batch",
            role="ambient",
            sender_name=f"群友{index}",
            content=f"[群友{index}]: 第{index}条待处理消息",
            message_id=f"no-reply-{index}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ))
    db_session.commit()
    current = _pending_payload([
        GroupPendingMessage(
            sender_id="u1",
            sender_name="群友1",
            message="第1条待处理消息",
            message_id="no-reply-1",
            ts=created_at.timestamp(),
        ),
        GroupPendingMessage(
            sender_id="u2",
            sender_name="群友2",
            message="第2条待处理消息",
            message_id="no-reply-2",
            ts=created_at.timestamp(),
        ),
    ])["pending_text"]

    assert record_group_prompt_batch(
        db_session,
        group_user_id="group_no_reply_batch",
        source_message_ids=["no-reply-1", "no-reply-2"],
    ) is True
    messages, _debug = build_group_recent_messages(
        db_session,
        "group_no_reply_batch",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    assert len(messages) == 1
    assert messages[0]["content"] == ensure_user_input_block(current)
    marker = db_session.query(ChatLog).filter_by(
        message_id="no-reply-2"
    ).one()
    assert json.loads(marker.source_message_ids_json) == [
        "no-reply-1",
        "no-reply-2",
    ]
    assert record_group_prompt_batch(
        db_session,
        group_user_id="group_no_reply_batch",
        source_message_ids=["no-reply-1", "no-reply-2"],
    ) is False


def test_group_prompt_batch_keeps_reply_before_messages_arriving_during_generation(
    db_session,
):
    from app.group_ingress.helpers import record_group_prompt_batch
    from core.context_builder import build_group_recent_messages

    created_at = datetime(2026, 8, 13, 16, 26, 0)
    for index in range(1, 4):
        db_session.add(ChatLog(
            user_id="group_batch_causal",
            session_id="group_batch_causal",
            role="ambient",
            sender_name=f"群友{index}",
            content=f"[群友{index}]: 第{index}条消息",
            message_id=f"batch-causal-{index}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ))
    db_session.commit()
    record_group_prompt_batch(
        db_session,
        group_user_id="group_batch_causal",
        source_message_ids=["batch-causal-1", "batch-causal-2"],
    )
    db_session.add(ChatLog(
        user_id="group_batch_causal",
        session_id="group_batch_causal",
        role="assistant",
        sender_name="小南",
        content="这是对前两条消息的回复。",
        message_id="batch-causal-2",
        source_message_ids_json='["batch-causal-1", "batch-causal-2"]',
        created_at=created_at,
        processed=1,
        meta_json='{"kind":"group_reply"}',
    ))
    db_session.commit()

    messages, _debug = build_group_recent_messages(
        db_session,
        "group_batch_causal",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=24000,
    )

    assert [item["role"] for item in messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert messages[1]["content"] == "这是对前两条消息的回复。"
    assert messages[2]["message_id"] == "batch-causal-3"


@pytest.mark.parametrize("source_id_block_span", [4, 128])
def test_group_recent_window_rotates_on_fixed_source_id_blocks(
    db_session,
    source_id_block_span,
):
    from core.context_builder import (
        build_group_recent_messages,
        format_group_canonical_message,
    )
    from core.token_utils import estimate_tokens

    created_at = datetime(2026, 7, 31, 16, 30, 0)
    content = "固定块缓存边界" * 20
    for index in range(1, 12):
        db_session.add(ChatLog(
            id=index,
            user_id="group_block_cache",
            session_id="group_block_cache",
            role="assistant",
            sender_name="小南",
            content=content,
            message_id=f"m{index:02d}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"group_reply"}',
        ))
    db_session.commit()
    one_block_tokens = estimate_tokens(format_group_canonical_message(
        sender_name="小南",
        content=content,
        timestamp=created_at,
        message_id="m01",
        max_chars=12000,
    ))

    before, before_debug = build_group_recent_messages(
        db_session,
        "group_block_cache",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=one_block_tokens * 5,
        source_id_block_span=source_id_block_span,
    )
    db_session.add(ChatLog(
        id=12,
        user_id="group_block_cache",
        session_id="group_block_cache",
        role="assistant",
        sender_name="小南",
        content=content,
        message_id="m12",
        created_at=created_at,
        processed=1,
        meta_json='{"kind":"group_reply"}',
    ))
    db_session.commit()
    after, after_debug = build_group_recent_messages(
        db_session,
        "group_block_cache",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=one_block_tokens * 5,
        source_id_block_span=source_id_block_span,
    )

    before_ids = [item["source_id"] for item in before]
    after_ids = [item["source_id"] for item in after]
    assert before_ids == after_ids[:len(before_ids)]
    assert len(after_ids) == len(before_ids) + 1
    assert before_debug["group_recent_source_id_block_span"] == (
        source_id_block_span
    )
    assert before_debug["group_recent_block_aligned"] is True
    assert 1 < before_debug["group_recent_source_id_effective_block_span"] <= (
        source_id_block_span
    )
    assert before_debug["group_recent_source_id_block_scope"] == "session_local"
    assert after_debug["group_recent_block_aligned"] is True


def test_group_recent_window_blocks_are_isolated_from_other_sessions(db_session):
    from core.context_builder import (
        build_group_recent_messages,
        format_group_canonical_message,
    )
    from core.token_utils import estimate_tokens

    created_at = datetime(2026, 8, 13, 16, 40, 0)
    content = "会话内固定块" * 20
    for index in range(11):
        db_session.add(ChatLog(
            user_id="group_local_block",
            session_id="group_local_block",
            role="assistant",
            sender_name="小南",
            content=content,
            message_id=f"local-{index}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"group_reply"}',
        ))
    db_session.commit()
    one_message_tokens = estimate_tokens(format_group_canonical_message(
        sender_name="小南",
        content=content,
        timestamp=created_at,
        message_id="local-0",
        max_chars=12000,
    ))

    before, _ = build_group_recent_messages(
        db_session,
        "group_local_block",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=one_message_tokens * 5,
        source_id_block_span=4,
    )
    for index in range(20):
        db_session.add(ChatLog(
            user_id="group_unrelated",
            session_id="group_unrelated",
            role="ambient",
            sender_name="路人",
            content=f"无关消息{index}",
            message_id=f"unrelated-{index}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ))
    db_session.commit()
    after, _ = build_group_recent_messages(
        db_session,
        "group_local_block",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=one_message_tokens * 5,
        source_id_block_span=4,
    )

    assert after == before


def test_group_recent_window_respects_manifest_token_budget(db_session):
    from core.context_builder import build_group_recent_messages
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.section_renderer import stable_json
    from core.token_utils import estimate_tokens

    created_at = datetime(2026, 8, 13, 16, 0, 0)
    for index in range(48):
        db_session.add(ChatLog(
            user_id="group_manifest_budget",
            session_id="group_manifest_budget",
            role="ambient",
            sender_name=f"群友{index % 4}",
            content="缓存预算消息" * 12,
            message_id=f"manifest-{index:02d}",
            created_at=created_at,
            processed=1,
            meta_json='{"kind":"chat"}',
        ))
    db_session.commit()

    all_messages, all_debug = build_group_recent_messages(
        db_session,
        "group_manifest_budget",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=None,
    )
    all_payload = PromptCompileRequest(
        history_messages=all_messages,
    ).normalized_history_messages()
    manifest_tokens = estimate_tokens(stable_json(all_payload))
    content_tokens = all_debug["group_recent_tokens"]
    assert manifest_tokens > content_tokens

    messages, debug = build_group_recent_messages(
        db_session,
        "group_manifest_budget",
        limit=None,
        max_per_msg=12000,
        max_total=0,
        max_tokens=content_tokens,
        source_id_block_span=4,
    )
    payload = PromptCompileRequest(
        history_messages=messages,
    ).normalized_history_messages()
    actual_tokens = estimate_tokens(stable_json(payload))

    assert actual_tokens <= content_tokens
    assert debug["group_recent_manifest_tokens"] == actual_tokens
    assert len(debug["group_recent_source_ids"]) < 48
    assert debug["group_recent_block_aligned"] is True


@pytest.mark.asyncio
async def test_group_prompt_keeps_sender_specific_context_after_cached_history():
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest

    shared = {
        "platform": "qq",
        "chat_type": "group",
        "session_id": "group_cache_prefix",
        "group_id": "group_cache_prefix",
        "bot_name": "小南",
        "bot_aliases": ["南南"],
        "history_header": "<conversation_context>群聊历史</conversation_context>",
        "history_messages": [
            {"role": "user", "content": "<user_input>历史问题</user_input>"},
            {"role": "assistant", "content": "历史回答"},
        ],
        "session_guidance": "回答保持简洁。",
        "user_input": "当前问题",
    }
    first = await compile_prompt_plan(
        PromptCompileRequest(
            **shared,
            user_id="sender-cache-u1",
            sender_id="sender-cache-u1",
            sender_name="甲",
            persona_text="甲的画像",
            group_profile_context=(
                "<group_memory_context>甲查询命中的群资料</group_memory_context>"
            ),
            project_context="甲查询命中的项目资料",
            is_super_user=False,
        ),
        strict_audit=True,
    )
    second = await compile_prompt_plan(
        PromptCompileRequest(
            **shared,
            user_id="sender-cache-u2",
            sender_id="sender-cache-u2",
            sender_name="乙",
            persona_text="乙的画像",
            group_profile_context=(
                "<group_memory_context>乙查询命中的群资料</group_memory_context>"
            ),
            project_context="乙查询命中的项目资料",
            is_super_user=True,
        ),
        strict_audit=True,
    )

    def section(plan, node_id):
        return next(
            item for item in plan.flow_sections
            if item["node_id"] == node_id
        )

    first_history = section(first, "history_messages")
    second_history = section(second, "history_messages")
    first_history_end = max(first_history["message_indexes"])
    second_history_end = max(second_history["message_indexes"])
    assert first.messages[:first_history_end + 1] == (
        second.messages[:second_history_end + 1]
    )

    for plan in (first, second):
        history_end = max(section(plan, "history_messages")["message_indexes"])
        group_context_index = section(
            plan, "group_context"
        )["message_indexes"][0]
        project_context_index = section(
            plan, "project_context"
        )["message_indexes"][0]
        persona_index = section(plan, "persona_reference")["message_indexes"][0]
        runtime_index = section(plan, "runtime_context")["message_indexes"][0]
        current_index = section(plan, "current_user_event")["message_indexes"][0]
        assert (
            history_end
            < group_context_index
            < project_context_index
            < persona_index
            < runtime_index
            < current_index
        )

    stable_prefix = json.dumps(
        first.messages[:first_history_end + 1],
        ensure_ascii=False,
    )
    assert "sender-cache-u1" not in stable_prefix
    assert "sender-cache-u2" not in stable_prefix


@pytest.mark.asyncio
async def test_long_group_dialogue_keeps_over_90_percent_request_cacheable(
    db_session,
):
    from core.context_compaction import project_model_context
    from core.llm_trace_context import attach_prompt_prefix_cache_context
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.section_renderer import stable_json
    from core.token_utils import estimate_tokens
    from core.tool_plan import build_tool_plan
    from foundation.llm.cache_shape import build_llm_cache_shape
    from foundation.llm.request_sanitizer import sanitize_payload_messages
    from nanobot_kt.skill_runtime import build_skill_bridge_binding

    history_messages: list[dict[str, str]] = []
    for index in range(128):
        history_messages.extend([
            {
                "role": "user",
                "content": (
                    f"<user_input>第{index}轮问题："
                    + "稳定历史正文" * 10
                    + "</user_input>"
                ),
            },
            {
                "role": "assistant",
                "content": f"第{index}轮回答：" + "稳定回答正文" * 10,
            },
        ])

    common = {
        "platform": "qq",
        "chat_type": "group",
        "session_id": "group_long_cache_prefix",
        "group_id": "group_long_cache_prefix",
        "bot_name": "小南",
        "bot_aliases": ["南南"],
        "history_header": "<conversation_context>群聊历史</conversation_context>",
        "session_guidance": "回答保持简洁。",
    }
    base_tool_plan = build_tool_plan(
        chat_type="group",
        group_id="group_long_cache_prefix",
        platform="qq",
        session_id="group_long_cache_prefix",
        db=db_session,
    )
    first_skill = build_skill_bridge_binding(
        db=db_session,
        tool_plan=base_tool_plan,
        project_context="甲本轮查询命中的项目资料",
        platform="qq",
        runtime_chat_type="group",
        is_group=True,
        owner_id="group_long_cache_prefix",
        agent_id="nanobot",
        session_id="group_long_cache_prefix",
        query="你好，继续检查缓存",
    )
    second_skill = build_skill_bridge_binding(
        db=db_session,
        tool_plan=base_tool_plan,
        project_context="乙本轮查询命中的项目资料",
        platform="qq",
        runtime_chat_type="group",
        is_group=True,
        owner_id="group_long_cache_prefix",
        agent_id="nanobot",
        session_id="group_long_cache_prefix",
        query="查一下今天的 AI 新闻并检查下一轮缓存",
    )
    assert first_skill.tool_plan.sent_tool_schemas == (
        second_skill.tool_plan.sent_tool_schemas
    )
    first = await compile_prompt_plan(
        PromptCompileRequest(
            **common,
            user_id="long-cache-u1",
            sender_id="long-cache-u1",
            sender_name="甲",
            history_messages=history_messages,
            group_profile_context="甲本轮查询命中的群资料",
            project_context=first_skill.project_context,
            persona_text="甲本轮画像",
            tool_schemas=list(first_skill.tool_plan.sent_tool_schemas),
            current_message_id="long-current-1",
            event_time="2026-08-13 18:00:00 CST",
            user_input="继续检查缓存",
        ),
        strict_audit=True,
    )
    second = await compile_prompt_plan(
        PromptCompileRequest(
            **common,
            user_id="long-cache-u2",
            sender_id="long-cache-u2",
            sender_name="乙",
            history_messages=[
                *history_messages,
                {"role": "user", "content": first.current_user_content},
                {"role": "assistant", "content": "上一轮回答"},
            ],
            group_profile_context="乙本轮查询命中的群资料",
            project_context=second_skill.project_context,
            persona_text="乙本轮画像",
            tool_schemas=list(second_skill.tool_plan.sent_tool_schemas),
            current_message_id="long-current-2",
            event_time="2026-08-13 18:01:00 CST",
            user_input="检查下一轮缓存",
        ),
        strict_audit=True,
    )

    def history_end(plan) -> int:
        section = next(
            item
            for item in plan.flow_sections
            if item["node_id"] == "history_messages"
        )
        return max(section["message_indexes"])

    first_history_end = history_end(first)
    assert first.messages[:first_history_end + 1] == (
        second.messages[:first_history_end + 1]
    )

    first_projection = project_model_context(
        messages=first.messages,
        tools=first.tool_schemas,
    )
    second_projection = project_model_context(
        messages=second.messages,
        tools=second.tool_schemas,
    )
    assert first_projection.decision is None
    assert second_projection.decision is None
    first_wire = sanitize_payload_messages({
        "messages": list(first_projection.messages),
        "tools": first.tool_schemas,
    })
    second_wire = sanitize_payload_messages({
        "messages": list(second_projection.messages),
        "tools": second.tool_schemas,
    })
    assert first_wire == first.request_json
    assert second_wire == second.request_json
    assert first_wire["messages"][:first_history_end + 1] == (
        second_wire["messages"][:first_history_end + 1]
    )

    first_cache_context = attach_prompt_prefix_cache_context(
        {"session_id": common["session_id"]},
        first.prefix_cache_manifest,
        flow_sections=first.flow_sections,
    )
    second_cache_context = attach_prompt_prefix_cache_context(
        {"session_id": common["session_id"]},
        second.prefix_cache_manifest,
        flow_sections=second.flow_sections,
    )
    first_shape = build_llm_cache_shape(
        first_wire,
        cache_context=first_cache_context,
    )
    second_shape = build_llm_cache_shape(
        second_wire,
        cache_context=second_cache_context,
    )
    for shape in (first_shape, second_shape):
        assert shape["history_source"] == "manifest"
        assert shape["stable_prefix_contract_match"] is True
        assert shape["tool_schema_contract_match"] is True
    assert first_shape["stable_prefix_sha256"] == (
        second_shape["stable_prefix_sha256"]
    )
    assert first_shape["tools_sha256"] == second_shape["tools_sha256"]
    assert first_shape["history_head_sha256"] == (
        second_shape["history_head_sha256"]
    )

    stable_tokens = (
        estimate_tokens(stable_json(first_wire["messages"][:first_history_end + 1]))
        + estimate_tokens(stable_json(first_wire["tools"]))
    )
    request_tokens = estimate_tokens(
        stable_json(first_wire["messages"])
    ) + estimate_tokens(
        stable_json(first_wire["tools"])
    )
    assert stable_tokens / request_tokens >= 0.90


@pytest.mark.asyncio
async def test_long_private_dialogue_keeps_over_90_percent_request_cacheable(
    db_session,
):
    from core.context_compaction import project_model_context
    from core.llm_trace_context import attach_prompt_prefix_cache_context
    from core.prompt_v2.compiler import compile_prompt_plan
    from core.prompt_v2.schema import PromptCompileRequest
    from core.prompt_v2.section_renderer import stable_json
    from core.token_utils import estimate_tokens
    from core.tool_plan import build_tool_plan
    from foundation.llm.cache_shape import build_llm_cache_shape
    from foundation.llm.request_sanitizer import sanitize_payload_messages

    history_messages: list[dict[str, str]] = []
    # 私聊 recent_conversation 的生产预算是 8k token；使用接近上限但不越界的
    # 长窗口验证缓存比例，避免用编译器本就会拒绝的非生产输入制造假场景。
    for index in range(32):
        history_messages.extend([
            {
                "role": "user",
                "content": (
                    f"<user_input>第{index}轮私聊问题："
                    + "稳定私聊历史正文" * 8
                    + "</user_input>"
                ),
            },
            {
                "role": "assistant",
                "content": f"第{index}轮私聊回答：" + "稳定私聊回答正文" * 8,
            },
        ])

    common = {
        "platform": "qq",
        "chat_type": "private",
        "session_id": "private_long_cache_prefix",
        "user_id": "private-long-cache-u1",
        "sender_id": "private-long-cache-u1",
        "sender_name": "甲",
        "session_name": "私聊",
        "bot_name": "小南",
        "bot_aliases": ["南南"],
        "history_header": "<conversation_context>私聊历史</conversation_context>",
        "session_guidance": "回答保持简洁。",
        "summary_context": "固定摘要版本。",
    }
    tool_plan = build_tool_plan(
        chat_type="private",
        user_id=common["user_id"],
        platform="qq",
        session_id=common["session_id"],
        db=db_session,
    )
    tool_schemas = list(tool_plan.sent_tool_schemas)
    first = await compile_prompt_plan(
        PromptCompileRequest(
            **common,
            history_messages=history_messages,
            project_context="第一轮查询命中的项目资料",
            persona_text="第一轮用户画像",
            tool_schemas=tool_schemas,
            current_message_id="private-long-current-1",
            event_time="2026-08-13 18:00:00 CST",
            effort_constraint="认真回答第一轮问题。",
            user_input="继续检查私聊缓存",
        ),
        strict_audit=True,
    )
    second = await compile_prompt_plan(
        PromptCompileRequest(
            **common,
            history_messages=[
                *history_messages,
                {"role": "user", "content": first.current_user_content},
                {"role": "assistant", "content": "上一轮私聊回答"},
            ],
            project_context="第二轮查询命中的项目资料",
            persona_text="第二轮用户画像",
            tool_schemas=tool_schemas,
            current_message_id="private-long-current-2",
            event_time="2026-08-13 18:01:00 CST",
            effort_constraint="快速回答第二轮问题。",
            user_input="检查下一轮私聊缓存",
        ),
        strict_audit=True,
    )

    def history_end(plan) -> int:
        section = next(
            item
            for item in plan.flow_sections
            if item["node_id"] == "history_messages"
        )
        return max(section["message_indexes"])

    first_history_end = history_end(first)
    assert first.messages[:first_history_end + 1] == (
        second.messages[:first_history_end + 1]
    )

    first_projection = project_model_context(
        messages=first.messages,
        tools=first.tool_schemas,
    )
    second_projection = project_model_context(
        messages=second.messages,
        tools=second.tool_schemas,
    )
    assert first_projection.decision is None
    assert second_projection.decision is None
    first_wire = sanitize_payload_messages({
        "messages": list(first_projection.messages),
        "tools": first.tool_schemas,
    })
    second_wire = sanitize_payload_messages({
        "messages": list(second_projection.messages),
        "tools": second.tool_schemas,
    })
    assert first_wire == first.request_json
    assert second_wire == second.request_json
    assert first_wire["messages"][:first_history_end + 1] == (
        second_wire["messages"][:first_history_end + 1]
    )

    first_cache_context = attach_prompt_prefix_cache_context(
        {"session_id": common["session_id"]},
        first.prefix_cache_manifest,
        flow_sections=first.flow_sections,
    )
    second_cache_context = attach_prompt_prefix_cache_context(
        {"session_id": common["session_id"]},
        second.prefix_cache_manifest,
        flow_sections=second.flow_sections,
    )
    first_shape = build_llm_cache_shape(
        first_wire,
        cache_context=first_cache_context,
    )
    second_shape = build_llm_cache_shape(
        second_wire,
        cache_context=second_cache_context,
    )
    for shape in (first_shape, second_shape):
        assert shape["history_source"] == "manifest"
        assert shape["stable_prefix_contract_match"] is True
        assert shape["tool_schema_contract_match"] is True
    assert first_shape["stable_prefix_sha256"] == (
        second_shape["stable_prefix_sha256"]
    )
    assert first_shape["tools_sha256"] == second_shape["tools_sha256"]
    assert first_shape["history_head_sha256"] == (
        second_shape["history_head_sha256"]
    )

    stable_tokens = (
        estimate_tokens(stable_json(first_wire["messages"][:first_history_end + 1]))
        + estimate_tokens(stable_json(first_wire["tools"]))
    )
    request_tokens = estimate_tokens(
        stable_json(first_wire["messages"])
    ) + estimate_tokens(
        stable_json(first_wire["tools"])
    )
    assert stable_tokens / request_tokens >= 0.90
