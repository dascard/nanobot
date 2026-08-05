import json
import threading
from datetime import datetime, timedelta

import pytest

from core.database import (
    ChatLog,
    ConversationTurn,
    OutboundDeliveryControl,
    Persona,
    PersonaFact,
    ProactiveOutreachLog,
    User,
)


@pytest.fixture(autouse=True)
def _seed_proactive_outreach_delivery_control(db_session):
    db_session.add(OutboundDeliveryControl(
        source_type="proactive_outreach",
        mode="legacy_direct",
        cutover_epoch=0,
        effective_from=datetime(1970, 1, 1),
        protocol_version=2,
        writer_version=0,
    ))
    db_session.commit()


def test_build_outreach_grounding_includes_persona_recent_chat_and_intent(db_session):
    from core.proactive_outreach import build_outreach_grounding

    base = datetime(2026, 7, 6, 9, 0, 0)
    db_session.add(Persona(user_id="superuser", persona_json='{"likes": ["夜跑"], "tone": "自然"}'))
    db_session.add_all([
        ConversationTurn(
            user_id="superuser",
            session_id="private_superuser",
            role="user",
            content="我今晚想去夜跑。",
            created_at=base,
        ),
        ConversationTurn(
            user_id="other",
            session_id="private_other",
            role="user",
            content="不要出现在 grounding 里。",
            created_at=base + timedelta(minutes=1),
        ),
        ConversationTurn(
            user_id="superuser",
            session_id="private_superuser",
            role="assistant",
            content="那我晚点想起来可以问问你跑得怎么样。",
            created_at=base + timedelta(minutes=2),
        ),
        ProactiveOutreachLog(
            user_id="superuser",
            idempotency_key="outreach:superuser:last-plan",
            grounding_json="{}",
            judge_should=False,
            judge_reason="等晚点再问",
            next_intent="问问夜跑有没有顺利",
            status="pending",
            created_at=base + timedelta(minutes=3),
        ),
    ])
    db_session.commit()

    grounding = build_outreach_grounding("superuser", db=db_session, recent_limit=5)

    assert grounding["user_id"] == "superuser"
    assert grounding["persona"]["likes"] == ["夜跑"]
    assert grounding["next_intent"] == "问问夜跑有没有顺利"
    assert [item["content"] for item in grounding["recent_messages"]] == [
        "我今晚想去夜跑。",
        "那我晚点想起来可以问问你跑得怎么样。",
    ]
    assert grounding["recent_messages"][0]["role"] == "user"
    assert grounding["recent_messages"][1]["role"] == "assistant"


def test_build_outreach_grounding_includes_precomputed_time_anchors(db_session):
    from core.proactive_outreach import build_outreach_grounding

    now = datetime(2026, 7, 7, 15, 30, 0)
    db_session.add_all([
        ConversationTurn(
            user_id="superuser",
            session_id="private_superuser",
            role="user",
            content="今天上午说接口联调卡住了，晚点继续看。",
            created_at=datetime(2026, 7, 7, 9, 0, 0),
        ),
        ConversationTurn(
            user_id="superuser",
            session_id="private_superuser",
            role="assistant",
            content="那我下午想起来再问问你。",
            created_at=datetime(2026, 7, 7, 9, 5, 0),
        ),
        ProactiveOutreachLog(
            user_id="superuser",
            idempotency_key="outreach:superuser:sent-before",
            grounding_json="{}",
            judge_should=True,
            judge_reason="旧外呼",
            message="两天前主动问过你接口联调。",
            status="sent",
            forced=False,
            created_at=datetime(2026, 7, 5, 15, 30, 0),
        ),
    ])
    db_session.commit()

    grounding = build_outreach_grounding(
        "superuser",
        db=db_session,
        now=now,
        thread_extractor=lambda messages: ["接口联调卡住，晚点继续看"],
    )

    assert grounding["now"] == {
        "iso": "2026-07-07T15:30:00",
        "weekday": "星期二",
        "period": "午后",
        "hour": 15,
    }
    assert grounding["hours_since_last_user_message"] == pytest.approx(6.5)
    assert grounding["last_user_message"] == {
        "content": "今天上午说接口联调卡住了，晚点继续看。",
        "created_at": "2026-07-07T09:00:00",
        "hours_ago": pytest.approx(6.5),
    }
    assert grounding["days_since_last_outreach"] == pytest.approx(2.0)
    assert grounding["recent_threads"] == ["接口联调卡住，晚点继续看"]
    assert grounding["recent_outreaches"] == [{
        "status": "sent",
        "forced": False,
        "message": "两天前主动问过你接口联调。",
        "next_intent": "",
        "created_at": "2026-07-05T15:30:00",
    }]


def test_build_outreach_grounding_includes_only_active_auto_persona_facts(
    db_session,
):
    from core.proactive_outreach import build_outreach_grounding

    now = datetime(2026, 7, 7, 15, 30, 0)
    active_fact = PersonaFact(
        user_id="persona-fact-user",
        content="喜欢研究 Agent 长期记忆",
        fact_type="preference",
        confidence="确认",
        evidence_count=3,
        status="active",
        inject_policy="auto",
        last_seen=now - timedelta(days=1),
    )
    db_session.add_all([
        active_fact,
        PersonaFact(
            user_id="persona-fact-user",
            content="只允许人工使用的偏好",
            status="active",
            inject_policy="manual_only",
        ),
        PersonaFact(
            user_id="persona-fact-user",
            content="仍在审核的候选事实",
            status="review",
            inject_policy="auto",
        ),
    ])
    db_session.commit()

    grounding = build_outreach_grounding(
        "persona-fact-user",
        db=db_session,
        now=now,
        thread_extractor=lambda _messages: [],
    )

    assert grounding["persona_facts"] == [{
        "evidence_id": f"persona_fact:{active_fact.id}",
        "content": "喜欢研究 Agent 长期记忆",
        "fact_type": "preference",
        "confidence": "确认",
        "evidence_count": 3,
        "last_seen": "2026-07-06T15:30:00",
    }]


def test_grounding_keeps_last_sent_message_when_newer_pending_exists(db_session):
    from core.proactive_outreach import build_outreach_grounding

    now = datetime(2026, 7, 10, 12, 0, 0)
    db_session.add_all([
        ProactiveOutreachLog(
            user_id="grounding-last-sent",
            idempotency_key="outreach:grounding-last-sent:sent",
            status="sent_after_ambiguous_replay",
            message="两天前真正发送的内容",
            next_intent="",
            created_at=now - timedelta(days=2),
        ),
        ProactiveOutreachLog(
            user_id="grounding-last-sent",
            idempotency_key="outreach:grounding-last-sent:pending",
            status="pending",
            message="",
            next_intent="等待新的具体话题",
            created_at=now - timedelta(hours=1),
        ),
    ])
    db_session.commit()

    grounding = build_outreach_grounding(
        "grounding-last-sent",
        db=db_session,
        now=now,
        thread_extractor=lambda _messages: [],
    )

    assert grounding["days_since_last_outreach"] == pytest.approx(2.0)
    assert grounding["last_outreach"]["status"] == "sent_after_ambiguous_replay"
    assert grounding["last_outreach"]["message"] == "两天前真正发送的内容"
    assert [item["message"] for item in grounding["recent_outreaches"]] == [
        "两天前真正发送的内容"
    ]
    assert grounding["next_intent"] == "等待新的具体话题"


def test_grounding_does_not_duplicate_delivery_context_in_recent_messages(db_session):
    from core.proactive_outreach import build_outreach_grounding

    now = datetime(2026, 7, 18, 12, 0, 0)
    db_session.add_all([
        ConversationTurn(
            user_id="delivery-context-user",
            session_id="private_delivery-context-user",
            role="user",
            content="最近在处理接口联调。",
            created_at=now - timedelta(hours=3),
        ),
        ConversationTurn(
            user_id="delivery-context-user",
            session_id="private_delivery-context-user",
            role="assistant",
            content="[主动外呼已发送] 刚才已经问过接口联调。",
            meta_json='{"kind":"outbound_delivery_summary"}',
            created_at=now - timedelta(hours=2),
        ),
        ProactiveOutreachLog(
            user_id="delivery-context-user",
            idempotency_key="outreach:delivery-context-user:sent",
            message="刚才已经问过接口联调。",
            status="sent",
            created_at=now - timedelta(hours=2),
        ),
    ])
    db_session.commit()

    grounding = build_outreach_grounding(
        "delivery-context-user",
        db=db_session,
        now=now,
        thread_extractor=lambda messages: [
            item["content"] for item in messages
        ],
    )

    assert [item["content"] for item in grounding["recent_messages"]] == [
        "最近在处理接口联调。"
    ]
    assert grounding["recent_threads"] == ["最近在处理接口联调。"]
    assert [item["message"] for item in grounding["recent_outreaches"]] == [
        "刚才已经问过接口联调。"
    ]


def test_extract_recent_threads_uses_injected_llm_call():
    from core.proactive_outreach import extract_recent_threads

    calls = []

    def fake_llm_call(**kwargs):
        calls.append(kwargs)
        return (
            '[{"topic":"接口联调卡住，晚点继续看","status":"open",'
            '"evidence_message_indexes":[0,1]},'
            '{"topic":"晚上可能去夜跑","status":"unknown",'
            '"evidence_message_indexes":[0]}]'
        )

    recent_messages = [
        {
            "role": "user",
            "content": "接口联调卡住了，下午继续看。",
            "created_at": "2026-07-06T10:00:00",
        },
        {
            "role": "model",
            "content": "那我晚点想起来问问你。",
            "created_at": "2026-07-06T10:05:00",
        },
    ]

    threads = extract_recent_threads(recent_messages, llm_call=fake_llm_call)

    assert threads == [
        {
            "evidence_id": "recent_thread:0",
            "topic": "接口联调卡住，晚点继续看",
            "status": "open",
            "evidence_message_indexes": [0, 1],
            "updated_at": "2026-07-06T10:05:00",
        },
        {
            "evidence_id": "recent_thread:1",
            "topic": "晚上可能去夜跑",
            "status": "unknown",
            "evidence_message_indexes": [0],
            "updated_at": "2026-07-06T10:00:00",
        },
    ]
    assert calls[0]["route_key"] == "outreach_extract"
    assert "open|completed|dismissed|unknown" in calls[0]["system_prompt"]
    payload = json.loads(calls[0]["user_message"])
    assert payload[0]["message_index"] == 0
    assert payload[1]["message_index"] == 1


def test_extract_recent_threads_preserves_completed_and_dismissed_lifecycle():
    from core.proactive_outreach import extract_recent_threads

    response = (
        '[{"topic":"接口调试","status":"completed",'
        '"evidence_message_indexes":[0,1]},'
        '{"topic":"状态文件排查","status":"dismissed",'
        '"evidence_message_indexes":[2,3]}]'
    )
    threads = extract_recent_threads(
        [
            {"role": "user", "content": "接口还要调试", "created_at": "2026-07-06T09:00:00"},
            {"role": "user", "content": "刚才已经调试好了", "created_at": "2026-07-06T10:00:00"},
            {"role": "assistant", "content": "要继续看状态文件吗", "created_at": "2026-07-06T10:05:00"},
            {"role": "user", "content": "先不管吧", "created_at": "2026-07-06T10:06:00"},
        ],
        llm_call=lambda **_kwargs: response,
    )

    assert [item["status"] for item in threads] == ["completed", "dismissed"]
    assert threads[0]["updated_at"] == "2026-07-06T10:00:00"
    assert threads[1]["updated_at"] == "2026-07-06T10:06:00"


def test_extract_recent_threads_rejects_assistant_only_evidence():
    from core.proactive_outreach import extract_recent_threads

    threads = extract_recent_threads(
        [
            {
                "role": "assistant",
                "content": "我刚跑了几个实验，晚点继续告诉你结果。",
                "created_at": "2026-07-06T10:00:00",
            },
            {
                "role": "user",
                "content": "好的。",
                "created_at": "2026-07-06T10:01:00",
            },
        ],
        llm_call=lambda **_kwargs: (
            '[{"topic":"实验进展","status":"open",'
            '"evidence_message_indexes":[0]}]'
        ),
    )

    assert threads == []


def test_extract_recent_threads_returns_empty_when_llm_fails():
    from core.proactive_outreach import extract_recent_threads

    def fail_llm_call(**kwargs):
        raise RuntimeError("model down")

    assert extract_recent_threads(
        [{"role": "user", "content": "今天有个项目要收尾。"}],
        llm_call=fail_llm_call,
    ) == []


def test_extract_recent_threads_returns_empty_for_no_messages():
    from core.proactive_outreach import extract_recent_threads

    calls = []

    def fake_llm_call(**kwargs):
        calls.append(kwargs)
        return '["不应该调用"]'

    assert extract_recent_threads([], llm_call=fake_llm_call) == []
    assert calls == []


def test_active_hours_uses_conversation_turn_distribution_and_default_when_sparse(db_session):
    from core.proactive_outreach import active_hours

    base = datetime(2026, 7, 6, 0, 0, 0)
    active_sample_hours = [9, 9, 10, 20, 20]
    for index, hour in enumerate(active_sample_hours):
        db_session.add(ConversationTurn(
            user_id="superuser",
            session_id="private_superuser",
            role="user",
            content=f"第 {index} 条活跃样本",
            created_at=base.replace(hour=hour, minute=index),
        ))
    db_session.add(ConversationTurn(
        user_id="other",
        session_id="private_other",
        role="user",
        content="其他用户不应影响统计",
        created_at=base.replace(hour=3),
    ))
    db_session.add(ConversationTurn(
        user_id="sparse",
        session_id="private_sparse",
        role="user",
        content="样本不足",
        created_at=base.replace(hour=2),
    ))
    db_session.commit()

    assert active_hours("superuser", db=db_session) == {8, 9, 10, 11, 19, 20, 21}
    assert active_hours("sparse", db=db_session) == set(range(8, 23))


def test_active_hours_window_intersects_default_two_hour_scheduler_cadence(db_session):
    from core.proactive_outreach import active_hours

    base = datetime(2026, 7, 6, 0, 0, 0)
    for index in range(5):
        db_session.add(ConversationTurn(
            user_id="cadence-user",
            session_id="private_cadence-user",
            role="user",
            content=f"九点活跃样本 {index}",
            created_at=base.replace(hour=9, minute=index),
        ))
    db_session.commit()

    hours = active_hours("cadence-user", db=db_session)
    ticks = [base + timedelta(hours=8 + offset) for offset in range(0, 48, 2)]

    assert hours == {8, 9, 10}
    assert any(tick.hour in hours for tick in ticks)


def test_judge_outreach_uses_dedicated_route_and_clamps_next_check_at(monkeypatch):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    calls = []

    def fake_call_model_route(**kwargs):
        calls.append(kwargs)
        return (
            '{"should_reach_out": true, "reason": "想问夜跑", '
            '"next_check_at": "2026-07-13T12:00:00", "next_intent": "问夜跑", '
            '"outreach_kind": "message", "research_query": "", '
            '"topic_type":"follow_up","topic":"今晚夜跑",'
            '"evidence_ids":["recent_thread:0"]}'
        )

    result = proactive_outreach.judge_outreach(
        {
            "user_id": "superuser",
            "recent_messages": [],
            "recent_threads": [{
                "evidence_id": "recent_thread:0",
                "topic": "今晚夜跑",
                "status": "open",
            }],
        },
        now=now,
        min_interval_min=30,
        max_check_interval_min=1440,
        model_call=fake_call_model_route,
    )

    assert result["should_reach_out"] is True
    assert result["reason"] == "想问夜跑"
    assert result["next_intent"] == "问夜跑"
    assert result["topic_type"] == "follow_up"
    assert result["topic"] == "今晚夜跑"
    assert result["evidence_ids"] == ["recent_thread:0"]
    assert result["next_check_at"] == "2026-07-07T12:00:00"
    assert calls[0]["route_key"] == "outreach_judge"
    assert "只表示最长静默已触发本轮强制评估" in calls[0]["system_prompt"]
    assert "不表示必须发送" in calls[0]["system_prompt"]
    assert "普通跟进只能选择 status=open" in calls[0]["system_prompt"]
    assert "禁止" not in calls[0]["system_prompt"]
    assert "黑名单" not in calls[0]["system_prompt"]
    assert "shadow" not in calls[0]["system_prompt"].lower()


def test_judge_outreach_rejects_prompt_example_multivalue_kind():
    from core import proactive_outreach

    result = proactive_outreach.judge_outreach(
        {"user_id": "contract-user", "recent_messages": []},
        now=datetime(2026, 7, 6, 12, 0, 0),
        model_call=lambda **_kwargs: (
            '{"should_reach_out": true, "reason": "研究后跟进", '
            '"next_check_in_hours": 2, "next_intent": "继续跟进", '
            '"outreach_kind": "message|research", "research_query": "AI agent"}'
        ),
    )

    assert result["should_reach_out"] is None
    assert result["error_type"] == "contract_error"


def test_judge_outreach_rejects_completed_or_dismissed_topic_evidence():
    from core import proactive_outreach

    result = proactive_outreach.judge_outreach(
        {
            "user_id": "closed-topic-user",
            "recent_threads": [{
                "evidence_id": "recent_thread:0",
                "topic": "状态文件排查",
                "status": "dismissed",
                "evidence_message_indexes": [0, 1],
                "updated_at": "2026-07-06T10:06:00",
            }],
            "persona_facts": [],
        },
        now=datetime(2026, 7, 6, 12, 0, 0),
        model_call=lambda **_kwargs: (
            '{"should_reach_out":true,"reason":"继续检查状态文件",'
            '"next_check_in_hours":2,"next_intent":"继续排查",'
            '"outreach_kind":"message","research_query":"",'
            '"topic_type":"follow_up","topic":"状态文件排查",'
            '"evidence_ids":["recent_thread:0"]}'
        ),
    )

    assert result["should_reach_out"] is None
    assert result["error_type"] == "contract_error"
    assert result["reason"] == "主动外呼 Judge 选择了无效或已关闭的事实依据"


def test_judge_outreach_accepts_discovery_from_active_persona_fact():
    from core import proactive_outreach

    result = proactive_outreach.judge_outreach(
        {
            "user_id": "discovery-user",
            "recent_threads": [],
            "persona_facts": [{
                "evidence_id": "persona_fact:42",
                "content": "喜欢研究 Agent 长期记忆",
                "fact_type": "preference",
            }],
        },
        now=datetime(2026, 7, 6, 12, 0, 0),
        model_call=lambda **_kwargs: (
            '{"should_reach_out":true,"reason":"基于 persona_fact:42 分享新资料",'
            '"next_check_in_hours":2,"next_intent":"询问是否想继续了解",'
            '"outreach_kind":"research","research_query":"Agent 长期记忆最新实践",'
            '"topic_type":"discovery","topic":"Agent 长期记忆的新实践",'
            '"evidence_ids":["persona_fact:42"]}'
        ),
    )

    assert result["should_reach_out"] is True
    assert result["topic_type"] == "discovery"
    assert result["evidence_ids"] == ["persona_fact:42"]
    assert result["outreach_kind"] == "research"


def test_judge_outreach_sends_compact_grounding_only_as_user_message(monkeypatch):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    long_recent_message = "接口联调卡住了，" + ("这里是很长的原始聊天内容。" * 80)
    calls = []

    def fake_call_model_route(**kwargs):
        calls.append(kwargs)
        return (
            '{"should_reach_out": false, "reason": "晚点再问", '
            '"next_check_in_hours": 2, "next_intent": "问接口联调", '
            '"outreach_kind": "message", "research_query": "", '
            '"topic_type":"none","topic":"","evidence_ids":[]}'
        )

    result = proactive_outreach.judge_outreach(
        {
            "user_id": "superuser",
            "now": {"iso": now.isoformat(), "weekday": "星期一", "period": "午后", "hour": 12},
            "recent_threads": ["接口联调卡住，晚点继续看"],
            "recent_messages": [
                {"role": "user", "content": long_recent_message, "created_at": now.isoformat()},
            ],
            "last_user_message": {
                "content": long_recent_message,
                "created_at": now.isoformat(),
                "hours_ago": 3,
            },
            "hours_since_last_user_message": 3,
            "days_since_last_outreach": 1,
            "next_intent": "问接口联调",
        },
        now=now,
        model_call=fake_call_model_route,
    )

    assert result["next_check_at"] == "2026-07-06T14:00:00"
    assert calls[0]["route_key"] == "outreach_judge"
    assert "next_check_in_hours" in calls[0]["system_prompt"]
    assert "用户消息" in calls[0]["system_prompt"]
    assert "recent_threads" in calls[0]["user_message"]
    assert "接口联调卡住，晚点继续看" in calls[0]["user_message"]
    assert long_recent_message not in calls[0]["system_prompt"]
    assert long_recent_message not in calls[0]["user_message"]


def test_judge_outreach_converts_next_check_in_hours_to_iso_and_clamps(monkeypatch):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    responses = [
        '{"should_reach_out": false, "reason": "下午再想", '
        '"next_check_in_hours": 3, "next_intent": "问接口联调", '
        '"outreach_kind": "message", "research_query": "", '
        '"topic_type":"none","topic":"","evidence_ids":[]}',
        '{"should_reach_out": false, "reason": "太近了", '
        '"next_check_in_hours": 0.1, "next_intent": "稍后", '
        '"outreach_kind": "message", "research_query": "", '
        '"topic_type":"none","topic":"","evidence_ids":[]}',
        '{"should_reach_out": false, "reason": "太远了", '
        '"next_check_in_hours": 100, "next_intent": "明天", '
        '"outreach_kind": "message", "research_query": "", '
        '"topic_type":"none","topic":"","evidence_ids":[]}',
    ]

    def fake_call_model_route(**kwargs):
        return responses.pop(0)

    normal = proactive_outreach.judge_outreach(
        {"user_id": "superuser", "recent_threads": ["接口联调"]},
        now=now,
        min_interval_min=30,
        max_check_interval_min=1440,
        model_call=fake_call_model_route,
    )
    low = proactive_outreach.judge_outreach(
        {"user_id": "superuser", "recent_threads": ["接口联调"]},
        now=now,
        min_interval_min=30,
        max_check_interval_min=1440,
        model_call=fake_call_model_route,
    )
    high = proactive_outreach.judge_outreach(
        {"user_id": "superuser", "recent_threads": ["接口联调"]},
        now=now,
        min_interval_min=30,
        max_check_interval_min=60,
        model_call=fake_call_model_route,
    )

    assert normal["next_check_at"] == "2026-07-06T15:00:00"
    assert low["next_check_at"] == "2026-07-06T12:30:00"
    assert high["next_check_at"] == "2026-07-06T13:00:00"


def test_generate_outreach_message_passes_full_decision_and_fact_policy(monkeypatch):
    from core import proactive_outreach

    calls = []

    def fake_call_model_route(**kwargs):
        calls.append(kwargs)
        if kwargs["route_key"] == "outreach_generate":
            return "刚想起你说今晚要夜跑，想来问问风是不是舒服一点。"
        assert kwargs["route_key"] == "outreach_quality"
        return '{"approved":true,"reason":"所有事实均有 grounding 支持"}'

    decision = {
        "should_reach_out": True,
        "reason": "想问夜跑",
        "next_check_at": "2026-07-06T15:00:00",
        "next_intent": "下次问夜跑后的感受",
        "outreach_kind": "message",
        "research_query": "",
        "topic_type": "follow_up",
        "topic": "今晚夜跑",
        "evidence_ids": ["recent_thread:0"],
        "error_type": None,
    }
    message = proactive_outreach.generate_outreach_message(
        {
            "user_id": "superuser",
            "persona": {"likes": ["夜跑"]},
            "recent_threads": ["今晚可能去夜跑"],
            "last_outreach": {"message": "昨天已经问过接口联调了。"},
            "recent_messages": [{"content": "我今晚想去夜跑。"}],
        },
        decision,
        model_call=fake_call_model_route,
    )

    assert message == "刚想起你说今晚要夜跑，想来问问风是不是舒服一点。"
    assert [call["route_key"] for call in calls] == [
        "outreach_generate",
        "outreach_quality",
    ]
    payload = json.loads(calls[0]["user_message"])
    assert payload["decision"] == decision
    assert "事实来源" in calls[0]["system_prompt"]
    assert "没有调用工具" in calls[0]["system_prompt"]
    assert "执行过检查、实验、脚本或文件处理" in calls[0]["system_prompt"]
    assert "只有 status=open 的话题可用于继续跟进" in calls[0]["system_prompt"]
    assert "recent_threads" in calls[0]["system_prompt"]
    assert "recent_outreaches" in calls[0]["system_prompt"]
    assert "避免重复已发过的话题" in calls[0]["system_prompt"]
    quality_payload = json.loads(calls[1]["user_message"])
    assert quality_payload["candidate"] == message
    assert quality_payload["decision"] == decision
    assert "事实依据" in calls[1]["system_prompt"]
    assert "语义重复" in calls[1]["system_prompt"]


def test_generate_outreach_message_blocks_failed_quality_review():
    from core import proactive_outreach

    responses = iter([
        "我刚又跑了几个实验，还写脚本检查了状态文件。",
        '{"approved":false,"reason":"正文声称了没有工具证据的外部行动"}',
    ])

    with pytest.raises(proactive_outreach.OutreachModelContractError) as exc_info:
        proactive_outreach.generate_outreach_message(
            {
                "user_id": "superuser",
                "persona": {},
                "persona_facts": [],
                "recent_threads": [],
                "recent_messages": [],
                "recent_outreaches": [{
                    "message": "我又看了一眼状态文件。",
                    "status": "sent",
                }],
            },
            {
                "should_reach_out": True,
                "reason": "普通问候",
                "next_check_at": "2026-07-06T15:00:00",
                "next_intent": "",
                "outreach_kind": "message",
                "research_query": "",
                "error_type": None,
            },
            model_call=lambda **_kwargs: next(responses),
        )

    assert exc_info.value.error_type == "quality_rejected"


@pytest.mark.asyncio
async def test_failed_quality_review_never_reaches_publisher(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    responses = iter([
        "我刚又跑了实验并检查了状态文件。",
        '{"approved":false,"reason":"缺少本轮工具证据"}',
    ])
    original_generator = proactive_outreach.generate_outreach_message

    def guarded_generator(grounding, decision):
        return original_generator(
            grounding,
            decision,
            model_call=lambda **_kwargs: next(responses),
        )

    monkeypatch.setattr(
        proactive_outreach,
        "generate_outreach_message",
        guarded_generator,
    )
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    result = await proactive_outreach.run_outreach_once(
        "quality-rejected-user",
        db=db_session,
        now=now,
        max_silence_min=999999,
        thread_extractor=lambda _messages: [{
            "topic": "状态文件",
            "status": "open",
            "evidence_message_indexes": [0],
            "updated_at": now.isoformat(),
        }],
        judge_fn=lambda *_args, **_kwargs: {
            "should_reach_out": True,
            "reason": "跟进状态文件",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
        publisher=publisher,
    )

    assert result["status"] == "generation_error"
    assert result["error_type"] == "quality_rejected"
    assert published == []
    row = db_session.query(ProactiveOutreachLog).filter_by(
        user_id="quality-rejected-user",
    ).one()
    assert row.status == "failed"
    assert row.message == ""


def test_generate_outreach_message_sends_compact_grounding_only_as_user_message(monkeypatch):
    from core import proactive_outreach

    long_recent_message = "今天项目收尾有点累，" + ("这里是很长的原始聊天内容。" * 80)
    calls = []

    def fake_call_model_route(**kwargs):
        calls.append(kwargs)
        if kwargs["route_key"] == "outreach_generate":
            return "刚想起你说项目收尾有点累，想来轻轻敲一下。"
        return '{"approved":true,"reason":"事实有据且不重复"}'

    message = proactive_outreach.generate_outreach_message(
        {
            "user_id": "superuser",
            "persona": {"likes": ["夜跑"]},
            "recent_threads": ["项目收尾有点累"],
            "recent_messages": [
                {"role": "user", "content": long_recent_message, "created_at": "2026-07-06T12:00:00"},
            ],
            "last_user_message": {
                "content": long_recent_message,
                "created_at": "2026-07-06T12:00:00",
                "hours_ago": 4,
            },
            "last_outreach": {"message": "昨天已经问过接口联调了。"},
        },
        "想跟进项目收尾",
        model_call=fake_call_model_route,
    )

    assert message == "刚想起你说项目收尾有点累，想来轻轻敲一下。"
    assert calls[0]["route_key"] == "outreach_generate"
    assert "用户消息" in calls[0]["system_prompt"]
    assert "不得虚构自己的持续状态、情绪或线下经历" in calls[0]["system_prompt"]
    assert "表达当下的内在感受" not in calls[0]["system_prompt"]
    assert "recent_threads" in calls[0]["user_message"]
    assert "项目收尾有点累" in calls[0]["user_message"]
    assert long_recent_message not in calls[0]["system_prompt"]
    assert long_recent_message not in calls[0]["user_message"]


def test_generate_outreach_message_includes_bounded_recent_outreach_history():
    from core import proactive_outreach

    long_outreach = "上次已经分享过这个话题。" * 100
    calls = []

    def fake_call_model_route(**kwargs):
        calls.append(kwargs)
        if kwargs["route_key"] == "outreach_generate":
            return "这次换一个完全不同的话题。"
        return '{"approved":true,"reason":"新话题与历史外呼不重复"}'

    proactive_outreach.generate_outreach_message(
        {
            "recent_threads": ["换一个新话题"],
            "recent_outreaches": [
                {"message": long_outreach, "created_at": "2026-07-18T10:00:00"}
            ],
            "last_outreach": {"message": long_outreach},
        },
        "避免重复",
        model_call=fake_call_model_route,
    )

    payload = calls[0]["user_message"]
    assert "recent_outreaches" in payload
    assert long_outreach not in payload
    assert long_outreach[:300] in payload


@pytest.mark.asyncio
async def test_run_outreach_once_defers_same_topic_without_new_user_input(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 18, 12, 0, 0)
    last_user_at = now - timedelta(hours=4)
    last_user_content = "我的接口联调还没处理完。"
    db_session.add(ConversationTurn(
        user_id="repeat-topic-user",
        session_id="private_repeat-topic-user",
        role="user",
        content=last_user_content,
        created_at=last_user_at,
    ))
    db_session.add(ProactiveOutreachLog(
        user_id="repeat-topic-user",
        idempotency_key="outreach:repeat-topic-user:sent",
        grounding_json=(
            '{"last_user_message":{"content":"我的接口联调还没处理完。",'
            '"created_at":"2026-07-18T08:00:00"}}'
        ),
        judge_should=True,
        judge_reason="已跟进接口联调",
        next_intent="继续跟进接口联调",
        message="刚才已经问过接口联调的进展。",
        status="sent",
        forced=False,
        created_at=now - timedelta(hours=2),
    ))
    db_session.commit()

    def fake_judge(*_args, **_kwargs):
        return {
            "should_reach_out": True,
            "reason": "再问一次接口联调",
            "next_check_at": (now + timedelta(hours=3)).isoformat(),
            "next_intent": "继续跟进接口联调",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    def fail_generate(*_args, **_kwargs):
        raise AssertionError("重复话题应在生成前被拦截")

    result = await proactive_outreach.run_outreach_once(
        "repeat-topic-user",
        db=db_session,
        now=now,
        judge_fn=fake_judge,
        generator_fn=fail_generate,
        thread_extractor=lambda _messages: ["接口联调还没完成"],
        repeat_topic_cooldown_min=1440,
    )

    assert result["status"] == "skipped_repeated_topic"
    assert result["reason_code"] == "same_next_intent"
    assert result["next_check_at"] == "2026-07-19T10:00:00"
    pending = db_session.query(ProactiveOutreachLog).filter_by(status="pending").one()
    assert pending.message == ""
    assert pending.judge_reason == "重复话题门禁:same_next_intent"


def test_repeated_topic_guard_allows_same_intent_after_new_user_input(db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 18, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="new-anchor-user",
        idempotency_key="outreach:new-anchor-user:sent",
        grounding_json=(
            '{"last_user_message":{"content":"旧的接口联调进度",'
            '"created_at":"2026-07-18T08:00:00"}}'
        ),
        next_intent="继续跟进接口联调",
        message="已经跟进过旧进度。",
        status="sent",
        created_at=now - timedelta(hours=2),
    ))
    db_session.commit()

    guarded = proactive_outreach._repeated_topic_guard(
        db_session,
        user_id="new-anchor-user",
        grounding={
            "last_user_message": {
                "content": "刚有了新的接口联调结果",
                "created_at": "2026-07-18T11:30:00",
            }
        },
        judge={
            "next_intent": "继续跟进接口联调",
            "research_query": "",
        },
        now=now,
        cooldown_min=1440,
    )

    assert guarded is None


def test_repeated_topic_guard_ignores_outreach_before_history_clear(db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 18, 12, 0, 0)
    db_session.add(User(
        id="cleared-anchor-user",
        history_clear_at=now - timedelta(hours=1),
    ))
    db_session.add(ProactiveOutreachLog(
        user_id="cleared-anchor-user",
        idempotency_key="outreach:cleared-anchor-user:sent",
        grounding_json=(
            '{"last_user_message":{"content":"清除前的话题",'
            '"created_at":"2026-07-18T08:00:00"}}'
        ),
        next_intent="继续跟进清除前的话题",
        message="清除前已发送。",
        status="sent",
        created_at=now - timedelta(hours=2),
    ))
    db_session.commit()

    guarded = proactive_outreach._repeated_topic_guard(
        db_session,
        user_id="cleared-anchor-user",
        grounding={
            "last_user_message": {
                "content": "清除前的话题",
                "created_at": "2026-07-18T08:00:00",
            }
        },
        judge={
            "next_intent": "继续跟进清除前的话题",
            "research_query": "",
        },
        now=now,
        cooldown_min=1440,
    )

    assert guarded is None


@pytest.mark.asyncio
async def test_deliver_outreach_once_skips_duplicate_idempotency_key(monkeypatch, db_session):
    from core import proactive_outreach

    pushes = []

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    first = await proactive_outreach.deliver_outreach_once(
        user_id="superuser",
        idempotency_key="outreach:superuser:once",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="想问夜跑",
        next_check_at=datetime(2026, 7, 6, 13, 0, 0),
        next_intent="问夜跑",
        message="刚想起你说今晚要夜跑。",
        forced=False,
        db=db_session,
        publisher=fake_push_to_qq,
    )
    second = await proactive_outreach.deliver_outreach_once(
        user_id="superuser",
        idempotency_key="outreach:superuser:once",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="想问夜跑",
        next_check_at=datetime(2026, 7, 6, 13, 0, 0),
        next_intent="问夜跑",
        message="第二次不应该发送。",
        forced=False,
        db=db_session,
        publisher=fake_push_to_qq,
    )

    assert first["status"] == "sent"
    assert second["status"] == "skipped_duplicate"
    assert pushes == [("private", "superuser", "刚想起你说今晚要夜跑。")]
    rows = db_session.query(ProactiveOutreachLog).filter_by(idempotency_key="outreach:superuser:once").all()
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].message == "刚想起你说今晚要夜跑。"


@pytest.mark.asyncio
async def test_run_outreach_once_max_silence_forces_judge_but_respects_rejection(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:old",
        grounding_json="{}",
        judge_should=True,
        judge_reason="旧消息",
        message="两天前的消息",
        status="sent",
        forced=False,
        created_at=now - timedelta(hours=49),
    ))
    db_session.commit()

    judge_calls = []
    pushes = []

    def fake_judge(*args, **kwargs):
        judge_calls.append((args, kwargs))
        return {
            "should_reach_out": False,
            "reason": "当前没有足够的新信息",
            "next_check_at": (now + timedelta(days=7)).isoformat(),
            "next_intent": "等待用户产生新的上下文",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    def fail_generate(*_args, **_kwargs):
        raise AssertionError("Judge 拒绝后不得生成正文")

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fake_judge)
    monkeypatch.setattr(proactive_outreach, "generate_outreach_message", fail_generate)
    result = await proactive_outreach.run_outreach_once(
        "superuser",
        db=db_session,
        now=now,
        max_silence_min=2880,
        publisher=fake_push_to_qq,
    )

    assert result["status"] == "pending"
    assert result["forced"] is False
    assert len(judge_calls) == 1
    assert judge_calls[0][0][0]["trigger"]["kind"] == "max_silence_evaluation"
    assert judge_calls[0][0][0]["trigger"]["requires_delivery"] is False
    assert pushes == []

    pending_row = (
        db_session.query(ProactiveOutreachLog)
        .filter_by(user_id="superuser", status="pending")
        .one()
    )
    assert pending_row.forced is False
    assert pending_row.judge_reason == "当前没有足够的新信息"

    due_result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now + timedelta(hours=1),
        max_silence_min=2880,
    )

    assert due_result["status"] == "skipped_not_due"
    assert len(judge_calls) == 1


@pytest.mark.asyncio
async def test_run_outreach_once_max_silence_rechecks_stale_pending_with_judge(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:stale-pending",
        grounding_json="{}",
        judge_should=False,
        judge_reason="稍后再问",
        next_check_at=now + timedelta(days=7),
        next_intent="问问近况",
        message="",
        status="pending",
        forced=False,
        created_at=now - timedelta(hours=49),
    ))
    db_session.commit()

    judge_calls = []
    pushes = []

    def fake_judge(grounding, **_kwargs):
        judge_calls.append(grounding)
        return {
            "should_reach_out": False,
            "reason": "用户已经结束该话题",
            "next_check_at": (now + timedelta(hours=6)).isoformat(),
            "next_intent": "等待新话题",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    def fail_generate(*_args, **_kwargs):
        raise AssertionError("Judge 拒绝后不得生成正文")

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fake_judge)
    monkeypatch.setattr(proactive_outreach, "generate_outreach_message", fail_generate)
    result = await proactive_outreach.run_outreach_once(
        "superuser",
        db=db_session,
        now=now,
        max_silence_min=2880,
        publisher=fake_push_to_qq,
    )

    assert result["status"] == "pending"
    assert result["forced"] is False
    assert len(judge_calls) == 1
    assert judge_calls[0]["trigger"]["kind"] == "max_silence_evaluation"
    assert pushes == []
    rows = db_session.query(ProactiveOutreachLog).filter_by(
        user_id="superuser",
        status="pending",
    ).all()
    assert len(rows) == 1
    assert rows[0].judge_reason == "用户已经结束该话题"
    assert rows[0].forced is False

    due_result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now + timedelta(hours=1),
        max_silence_min=2880,
    )

    assert due_result["status"] == "skipped_not_due"
    assert len(judge_calls) == 1


@pytest.mark.asyncio
async def test_run_outreach_once_skips_when_min_interval_not_elapsed(monkeypatch, db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:recent",
        grounding_json="{}",
        judge_should=True,
        judge_reason="刚发过",
        message="十分钟前的消息",
        status="sent",
        forced=False,
        created_at=now - timedelta(minutes=10),
    ))
    db_session.commit()

    def fail_judge(*args, **kwargs):
        raise AssertionError("min_interval 内不应询问 Judge")

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fail_judge)

    result = await proactive_outreach.run_outreach_once(
        "superuser",
        db=db_session,
        now=now,
        min_interval_min=30,
    )

    assert result == {"status": "skipped_min_interval", "minutes_since_last": 10}


@pytest.mark.asyncio
async def test_run_outreach_due_once_skips_quiet_hours(monkeypatch, db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 2, 0, 0)
    base = datetime(2026, 7, 5, 0, 0, 0)
    for index, hour in enumerate([9, 9, 10, 20, 20]):
        db_session.add(ChatLog(
            user_id="superuser",
            role="user",
            content=f"活跃样本 {index}",
            created_at=base.replace(hour=hour, minute=index),
        ))
    db_session.commit()

    calls = []

    async def fail_run_outreach_once(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("安静时段不应触发主动外呼")

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fail_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once("superuser", db=db_session, now=now)

    assert result == {"status": "skipped_quiet_hours", "hour": 2}
    assert calls == []


@pytest.mark.asyncio
async def test_run_outreach_due_once_skips_until_next_check_at(monkeypatch, db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    next_check_at = now + timedelta(hours=1)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:future-plan",
        grounding_json="{}",
        judge_should=False,
        judge_reason="稍后再问",
        next_check_at=next_check_at,
        next_intent="问问下午的安排",
        message="",
        status="pending",
        forced=False,
        created_at=now - timedelta(minutes=5),
    ))
    db_session.commit()

    async def fail_run_outreach_once(*args, **kwargs):
        raise AssertionError("next_check_at 未到不应触发主动外呼")

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fail_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now,
    )

    assert result["status"] == "skipped_not_due"
    assert result["next_check_at"] == "2026-07-06T13:00:00"
    assert result["surge_roll"] is None


@pytest.mark.asyncio
async def test_run_outreach_due_once_respects_future_check_after_silence_evaluation(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    db_session.add_all([
        ProactiveOutreachLog(
            user_id="superuser",
            idempotency_key="outreach:superuser:old-sent",
            grounding_json="{}",
            judge_should=True,
            judge_reason="旧发送",
            message="很久前的消息",
            status="sent",
            forced=False,
            created_at=now - timedelta(hours=49),
        ),
        ProactiveOutreachLog(
            user_id="superuser",
            idempotency_key="outreach:superuser:future-plan-while-silent",
            grounding_json="{}",
            judge_should=False,
            judge_reason="稍后再问",
            next_check_at=now + timedelta(hours=2),
            next_intent="继续等待",
            status="pending",
            forced=False,
            created_at=now - timedelta(minutes=5),
        ),
    ])
    db_session.commit()

    calls = []

    async def fake_run_outreach_once(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "sent", "forced": True}

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fake_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now,
        max_silence_min=2880,
    )

    assert result["status"] == "skipped_not_due"
    assert result["next_check_at"] == "2026-07-06T14:00:00"
    assert calls == []


@pytest.mark.asyncio
async def test_run_outreach_due_once_never_sent_stale_pending_forces_evaluation(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:stale-future-plan",
        grounding_json="{}",
        judge_should=False,
        judge_reason="稍后再问",
        next_check_at=now + timedelta(hours=2),
        next_intent="继续等待",
        status="pending",
        forced=False,
        created_at=now - timedelta(hours=49),
    ))
    db_session.commit()

    calls = []

    async def fake_run_outreach_once(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "pending", "forced": False}

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fake_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now,
        max_silence_min=2880,
    )

    assert result == {"status": "pending", "forced": False}
    assert calls


@pytest.mark.asyncio
async def test_run_outreach_due_once_reuses_semantic_key_for_same_due_point(monkeypatch, db_session):
    from core import proactive_outreach

    due_anchor = datetime(2026, 7, 6, 12, 0, 0)
    first_now = due_anchor + timedelta(minutes=5)
    second_now = due_anchor + timedelta(minutes=6)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:due-plan",
        grounding_json="{}",
        judge_should=False,
        judge_reason="到点再问",
        next_check_at=due_anchor,
        next_intent="问问午饭",
        message="",
        status="pending",
        forced=False,
        created_at=due_anchor - timedelta(minutes=30),
    ))
    db_session.commit()

    pushes = []

    def fake_judge(*args, **kwargs):
        return {
            "should_reach_out": True,
            "reason": "到点问午饭",
            "next_check_at": due_anchor.isoformat(),
            "next_intent": "继续问午饭",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    def fake_generate(grounding, decision):
        return f"生成：{decision['reason']}"

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fake_judge)
    monkeypatch.setattr(proactive_outreach, "generate_outreach_message", fake_generate)
    first = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=first_now,
        min_interval_min=0,
        max_silence_min=999999,
        repeat_topic_cooldown_min=0,
        publisher=fake_push_to_qq,
    )
    second = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=second_now,
        min_interval_min=0,
        max_silence_min=999999,
        repeat_topic_cooldown_min=0,
        publisher=fake_push_to_qq,
    )

    assert first["status"] == "sent"
    assert second["status"] == "skipped_duplicate"
    assert pushes == [("private", "superuser", "生成：到点问午饭")]


@pytest.mark.asyncio
async def test_run_outreach_once_updates_existing_pending_schedule_when_judge_says_no(monkeypatch, db_session):
    from core import proactive_outreach

    first_now = datetime(2026, 7, 6, 12, 0, 0)
    second_now = first_now + timedelta(minutes=5)
    judge_results = [
        {
            "should_reach_out": False,
            "reason": "晚点再问午饭",
            "next_check_at": (first_now + timedelta(hours=1)).isoformat(),
            "next_intent": "问午饭",
        },
        {
            "should_reach_out": False,
            "reason": "改成晚饭再问",
            "next_check_at": (first_now + timedelta(hours=3)).isoformat(),
            "next_intent": "问晚饭",
        },
    ]

    def fake_judge(*args, **kwargs):
        return judge_results.pop(0)

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fake_judge)

    first = await proactive_outreach.run_outreach_once(
        "superuser",
        db=db_session,
        now=first_now,
        max_silence_min=999999,
    )
    second = await proactive_outreach.run_outreach_once(
        "superuser",
        db=db_session,
        now=second_now,
        max_silence_min=999999,
    )

    rows = (
        db_session.query(ProactiveOutreachLog)
        .filter_by(user_id="superuser", status="pending")
        .all()
    )
    assert first["status"] == "pending"
    assert second["status"] == "pending"
    assert len(rows) == 1
    assert rows[0].judge_reason == "改成晚饭再问"
    assert rows[0].next_check_at == first_now + timedelta(hours=3)
    assert rows[0].next_intent == "问晚饭"


@pytest.mark.asyncio
async def test_deliver_outreach_once_marks_request_boundary_before_push(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    observed_statuses = []

    async def fake_push_to_qq(target_type, target_id, message):
        row = (
            db_session.query(ProactiveOutreachLog)
            .filter_by(idempotency_key="outreach:superuser:sending-before-push")
            .one()
        )
        observed_statuses.append(row.status)
        return True

    result = await proactive_outreach.deliver_outreach_once(
        user_id="superuser",
        idempotency_key="outreach:superuser:sending-before-push",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="想问问",
        next_check_at=datetime(2026, 7, 6, 13, 0, 0),
        next_intent="问问",
        message="来敲一下门。",
        forced=False,
        db=db_session,
        publisher=fake_push_to_qq,
    )

    assert result["status"] == "sent"
    assert observed_statuses == ["delivering"]


@pytest.mark.asyncio
async def test_deliver_outreach_once_treats_sending_as_inflight_and_does_not_repush(monkeypatch, db_session):
    from core import proactive_outreach

    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:inflight",
        grounding_json="{}",
        judge_should=True,
        judge_reason="可能已经推送",
        next_check_at=datetime(2026, 7, 6, 13, 0, 0),
        next_intent="",
        message="这条可能已经发出去了。",
        status="sending",
        forced=False,
        created_at=datetime(2026, 7, 6, 12, 0, 0),
    ))
    db_session.commit()

    async def fail_push_to_qq(*args, **kwargs):
        raise AssertionError("sending 记录代表可能已投递，不应再次 push")

    result = await proactive_outreach.deliver_outreach_once(
        user_id="superuser",
        idempotency_key="outreach:superuser:inflight",
        grounding={"recent_messages": []},
        judge_should=True,
        judge_reason="重跑",
        next_check_at=datetime(2026, 7, 6, 13, 0, 0),
        next_intent="",
        message="不应该发第二次。",
        forced=False,
        db=db_session,
        publisher=fail_push_to_qq,
    )

    assert result["status"] == "skipped_duplicate"


@pytest.mark.asyncio
async def test_run_outreach_due_once_surge_hit_runs_judge_before_next_check(monkeypatch, db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    next_check_at = now + timedelta(hours=2)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:future-surge-hit",
        grounding_json="{}",
        judge_should=False,
        judge_reason="稍后再问",
        next_check_at=next_check_at,
        next_intent="问问下午",
        message="",
        status="pending",
        forced=False,
        created_at=now - timedelta(minutes=5),
    ))
    db_session.commit()

    calls = []

    async def fake_run_outreach_once(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "pending", "forced": False, "surge": True}

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fake_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now,
        allow_early_surge=True,
        surge_min_prob=0.5,
        surge_max_prob=0.5,
        random_fn=lambda: 0.25,
        max_silence_min=999999,
    )

    assert result == {"status": "pending", "forced": False, "surge": True}
    assert calls


@pytest.mark.asyncio
async def test_run_outreach_due_once_surge_miss_skips_until_next_check(monkeypatch, db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    next_check_at = now + timedelta(hours=2)
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:future-surge-miss",
        grounding_json="{}",
        judge_should=False,
        judge_reason="稍后再问",
        next_check_at=next_check_at,
        next_intent="问问下午",
        message="",
        status="pending",
        forced=False,
        created_at=now - timedelta(minutes=5),
    ))
    db_session.commit()

    async def fail_run_outreach_once(*args, **kwargs):
        raise AssertionError("冲击未命中时不应提前 Judge")

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fail_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now,
        allow_early_surge=True,
        surge_min_prob=0.1,
        surge_max_prob=0.1,
        random_fn=lambda: 0.99,
        max_silence_min=999999,
    )

    assert result["status"] == "skipped_not_due"
    assert result["next_check_at"] == "2026-07-06T14:00:00"


def test_surge_probability_increases_with_interaction_silence():
    from core.proactive_outreach import _surge_probability

    now = datetime(2026, 7, 6, 12, 0, 0)

    recent = _surge_probability(
        last_interaction_at=now - timedelta(minutes=10),
        now=now,
        min_prob=0.1,
        max_prob=0.6,
        ramp_minutes=1000,
    )
    old = _surge_probability(
        last_interaction_at=now - timedelta(minutes=1000),
        now=now,
        min_prob=0.1,
        max_prob=0.6,
        ramp_minutes=1000,
    )

    assert 0.1 <= recent < old <= 0.6
    assert old == pytest.approx(0.6)


@pytest.mark.asyncio
async def test_run_outreach_due_once_quiet_hours_does_not_roll_surge(monkeypatch, db_session):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 2, 0, 0)
    base = datetime(2026, 7, 5, 0, 0, 0)
    for index, hour in enumerate([9, 9, 10, 20, 20]):
        db_session.add(ChatLog(
            user_id="superuser",
            role="user",
            content=f"活跃样本 {index}",
            created_at=base.replace(hour=hour, minute=index),
        ))
    db_session.add(ProactiveOutreachLog(
        user_id="superuser",
        idempotency_key="outreach:superuser:quiet-future-plan",
        grounding_json="{}",
        judge_should=False,
        judge_reason="稍后再问",
        next_check_at=now + timedelta(hours=8),
        next_intent="问早上",
        message="",
        status="pending",
        forced=False,
        created_at=now - timedelta(minutes=5),
    ))
    db_session.commit()

    def fail_random():
        raise AssertionError("安静时段不应计算冲击随机数")

    async def fail_run_outreach_once(*args, **kwargs):
        raise AssertionError("安静时段不应提前 Judge")

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fail_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now,
        surge_min_prob=1.0,
        surge_max_prob=1.0,
        random_fn=fail_random,
        max_silence_min=999999,
    )

    assert result == {"status": "skipped_quiet_hours", "hour": 2}


@pytest.mark.asyncio
async def test_max_silence_delivers_existing_candidate_once_without_forced_followup(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    candidate = ProactiveOutreachLog(
        user_id="stale-candidate-user",
        idempotency_key="outreach:stale-candidate",
        grounding_json='{"recent_threads":["旧候选话题"]}',
        judge_should=True,
        judge_reason="旧候选",
        next_check_at=now - timedelta(hours=48),
        next_intent="",
        message="只应发送一次的旧候选",
        status="candidate",
        forced=False,
        created_at=now - timedelta(hours=49),
    )
    db_session.add(candidate)
    db_session.commit()
    published = []

    async def publisher(*args):
        published.append(args)
        return True

    def fail_generator(*_args, **_kwargs):
        raise AssertionError("已有 candidate 时不得另行生成 forced 消息")

    first = await proactive_outreach.run_outreach_once(
        "stale-candidate-user",
        db=db_session,
        now=now,
        max_silence_min=2880,
        generator_fn=fail_generator,
        publisher=publisher,
    )
    second = await proactive_outreach.run_outreach_once(
        "stale-candidate-user",
        db=db_session,
        now=now + timedelta(hours=1),
        max_silence_min=2880,
        thread_extractor=lambda _messages: [],
        judge_fn=lambda _grounding, *, now, **_kwargs: {
            "should_reach_out": False,
            "reason": "刚发送过，不再发送",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        },
        generator_fn=fail_generator,
        publisher=publisher,
    )

    db_session.expire_all()
    stored = db_session.get(ProactiveOutreachLog, candidate.id)
    assert first["status"] == "sent"
    assert second["status"] == "pending"
    assert published == [
        ("private", "stale-candidate-user", "只应发送一次的旧候选")
    ]
    assert stored.status == "sent"
    assert stored.created_at == now


@pytest.mark.asyncio
async def test_legacy_forced_candidate_is_cancelled_and_rejudged(
    monkeypatch,
    db_session,
):
    from core import proactive_outreach

    now = datetime(2026, 7, 10, 12, 0, 0)
    legacy = ProactiveOutreachLog(
        user_id="legacy-forced-candidate-user",
        idempotency_key="outreach:legacy-forced-candidate",
        grounding_json='{"recent_threads":[]}',
        judge_should=True,
        judge_reason="旧强制发送",
        next_check_at=now - timedelta(hours=48),
        next_intent="",
        message="不应投递的遗留强制候选",
        status="candidate",
        forced=True,
        created_at=now - timedelta(hours=49),
    )
    db_session.add(legacy)
    db_session.commit()
    judge_calls = []
    published = []

    def reject_after_recheck(grounding, *, now, **_kwargs):
        judge_calls.append(grounding)
        return {
            "should_reach_out": False,
            "reason": "没有可靠的新选题",
            "next_check_at": (now + timedelta(hours=2)).isoformat(),
            "next_intent": "",
            "outreach_kind": "message",
            "research_query": "",
            "error_type": None,
        }

    async def publisher(*args):
        published.append(args)
        return True

    monkeypatch.setattr(proactive_outreach, "judge_outreach", reject_after_recheck)
    result = await proactive_outreach.run_outreach_once(
        "legacy-forced-candidate-user",
        db=db_session,
        now=now,
        max_silence_min=2880,
        publisher=publisher,
    )

    db_session.expire_all()
    stored = db_session.get(ProactiveOutreachLog, legacy.id)
    assert result["status"] == "pending"
    assert result["forced"] is False
    assert len(judge_calls) == 1
    assert published == []
    assert stored.status == "cancelled"


def test_scheduler_threads_ambiguity_hold_setting_into_due_runner(monkeypatch):
    from core import proactive_outreach

    stop_event = threading.Event()
    calls = []
    legacy_drain_calls = []

    async def fake_legacy_drain():
        legacy_drain_calls.append("drain")
        return []

    async def fake_due_once(user_id, **kwargs):
        stop_event.set()
        calls.append((user_id, kwargs))
        return {"status": "pending"}

    int_values = {
        "proactive_outreach.fallback_interval_min": 120,
        "proactive_outreach.min_interval_min": 30,
        "proactive_outreach.max_check_interval_min": 1440,
        "proactive_outreach.max_silence_min": 2880,
        "proactive_outreach.ambiguous_hold_min": 75,
        "proactive_outreach.repeat_topic_cooldown_min": 720,
    }
    monkeypatch.setattr(
        proactive_outreach.settings,
        "get_bool",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        proactive_outreach.settings,
        "get_int",
        lambda key, default: int_values.get(key, default),
    )
    monkeypatch.setattr(
        proactive_outreach.settings,
        "get_float",
        lambda _key, default: default,
    )
    monkeypatch.setattr(
        proactive_outreach,
        "get_super_user_ids",
        lambda: {"scheduler-user"},
    )
    monkeypatch.setattr(proactive_outreach, "run_outreach_due_once", fake_due_once)
    monkeypatch.setattr(
        proactive_outreach,
        "drain_due_legacy_proactive_outboxes",
        fake_legacy_drain,
        raising=False,
    )

    proactive_outreach.proactive_outreach_scheduler(stop_event)

    assert legacy_drain_calls == []
    assert calls == [(
        "scheduler-user",
        {
            "min_interval_min": 30,
            "max_check_interval_min": 1440,
            "max_silence_min": 2880,
            "ambiguous_hold_min": 75,
            "repeat_topic_cooldown_min": 720,
            "daily_delivery_quota": (
                proactive_outreach.DEFAULT_DAILY_DELIVERY_QUOTA
            ),
            "allow_early_surge": True,
            "surge_min_prob": proactive_outreach.DEFAULT_SURGE_MIN_PROB,
            "surge_max_prob": proactive_outreach.DEFAULT_SURGE_MAX_PROB,
        },
    )]


def test_disabled_scheduler_does_not_load_worker_poll_or_drain(monkeypatch):
    from core import proactive_outreach

    class OneCycleEvent:
        def __init__(self):
            self.waits = []

        def is_set(self):
            return bool(self.waits)

        def wait(self, timeout):
            self.waits.append(timeout)
            return True

    stop_event = OneCycleEvent()
    forbidden_calls = []

    async def fake_legacy_drain():
        forbidden_calls.append("drain")
        return []

    def fake_worker_poll_interval():
        forbidden_calls.append("worker-config")
        return 1.0

    monkeypatch.setattr(
        proactive_outreach.settings,
        "get_bool",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        proactive_outreach.settings,
        "get_int",
        lambda key, default: (
            120
            if key == "proactive_outreach.fallback_interval_min"
            else default
        ),
    )
    monkeypatch.setattr(
        proactive_outreach,
        "drain_due_legacy_proactive_outboxes",
        fake_legacy_drain,
    )
    monkeypatch.setattr(
        proactive_outreach,
        "_legacy_drain_poll_interval_seconds",
        fake_worker_poll_interval,
        raising=False,
    )

    proactive_outreach.proactive_outreach_scheduler(stop_event)

    assert forbidden_calls == []
    assert stop_event.waits == [60.0]
