from datetime import datetime, timedelta

import pytest

from core.database import ChatLog, Persona, ProactiveOutreachLog


def test_build_outreach_grounding_includes_persona_recent_chat_and_intent(db_session):
    from core.proactive_outreach import build_outreach_grounding

    base = datetime(2026, 7, 6, 9, 0, 0)
    db_session.add(Persona(user_id="superuser", persona_json='{"likes": ["夜跑"], "tone": "自然"}'))
    db_session.add_all([
        ChatLog(
            user_id="superuser",
            session_id="private_superuser",
            sender_name="主人",
            role="user",
            content="我今晚想去夜跑。",
            created_at=base,
        ),
        ChatLog(
            user_id="other",
            session_id="private_other",
            sender_name="别人",
            role="user",
            content="不要出现在 grounding 里。",
            created_at=base + timedelta(minutes=1),
        ),
        ChatLog(
            user_id="superuser",
            session_id="private_superuser",
            sender_name="nanobot",
            role="model",
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
    assert grounding["recent_messages"][1]["role"] == "model"


def test_active_hours_uses_chat_log_distribution_and_default_when_sparse(db_session):
    from core.proactive_outreach import active_hours

    base = datetime(2026, 7, 6, 0, 0, 0)
    active_sample_hours = [9, 9, 10, 20, 20]
    for index, hour in enumerate(active_sample_hours):
        db_session.add(ChatLog(
            user_id="superuser",
            role="user",
            content=f"第 {index} 条活跃样本",
            created_at=base.replace(hour=hour, minute=index),
        ))
    db_session.add(ChatLog(
        user_id="other",
        role="user",
        content="其他用户不应影响统计",
        created_at=base.replace(hour=3),
    ))
    db_session.add(ChatLog(
        user_id="sparse",
        role="user",
        content="样本不足",
        created_at=base.replace(hour=2),
    ))
    db_session.commit()

    assert active_hours("superuser", db=db_session) == {9, 10, 20}
    assert active_hours("sparse", db=db_session) == set(range(8, 23))


def test_judge_outreach_uses_timing_proactive_and_clamps_next_check_at(monkeypatch):
    from core import proactive_outreach

    now = datetime(2026, 7, 6, 12, 0, 0)
    calls = []

    def fake_call_model_route(**kwargs):
        calls.append(kwargs)
        return (
            '{"should_reach_out": true, "reason": "想问夜跑", '
            '"next_check_at": "2026-07-13T12:00:00", "next_intent": "问夜跑"}'
        )

    monkeypatch.setattr(proactive_outreach, "call_model_route", fake_call_model_route)

    result = proactive_outreach.judge_outreach(
        {"user_id": "superuser", "recent_messages": []},
        now=now,
        min_interval_min=30,
        max_check_interval_min=1440,
    )

    assert result["should_reach_out"] is True
    assert result["reason"] == "想问夜跑"
    assert result["next_intent"] == "问夜跑"
    assert result["next_check_at"] == "2026-07-07T12:00:00"
    assert calls[0]["route_key"] == "timing_proactive"
    assert "禁止" not in calls[0]["system_prompt"]
    assert "黑名单" not in calls[0]["system_prompt"]
    assert "shadow" not in calls[0]["system_prompt"].lower()


def test_generate_outreach_message_uses_reply_route_and_positive_prompt(monkeypatch):
    from core import proactive_outreach

    calls = []

    def fake_call_model_route(**kwargs):
        calls.append(kwargs)
        return "刚想起你说今晚要夜跑，想来问问风是不是舒服一点。"

    monkeypatch.setattr(proactive_outreach, "call_model_route", fake_call_model_route)

    message = proactive_outreach.generate_outreach_message(
        {"user_id": "superuser", "recent_messages": [{"content": "我今晚想去夜跑。"}]},
        "想问夜跑",
    )

    assert message == "刚想起你说今晚要夜跑，想来问问风是不是舒服一点。"
    assert calls[0]["route_key"] == "reply"
    assert "可以表达你自己的状态和情绪" in calls[0]["system_prompt"]
    forbidden_markers = ["禁止", "黑名单", "语义越界", "情感依赖", "shadow", "dry-run"]
    assert all(marker not in calls[0]["system_prompt"] for marker in forbidden_markers)


@pytest.mark.asyncio
async def test_deliver_outreach_once_skips_duplicate_idempotency_key(monkeypatch, db_session):
    from core import proactive_outreach

    pushes = []

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_outreach, "push_to_qq", fake_push_to_qq)

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
    )

    assert first["status"] == "sent"
    assert second["status"] == "skipped_duplicate"
    assert pushes == [("private", "superuser", "刚想起你说今晚要夜跑。")]
    rows = db_session.query(ProactiveOutreachLog).filter_by(idempotency_key="outreach:superuser:once").all()
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].message == "刚想起你说今晚要夜跑。"


@pytest.mark.asyncio
async def test_run_outreach_once_forces_message_after_max_silence(monkeypatch, db_session):
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
            "reason": "模型还想拖延",
            "next_check_at": (now + timedelta(days=7)).isoformat(),
            "next_intent": "继续拖延",
        }

    def fake_generate(grounding, reason):
        assert reason == "超过最长沉默窗口，主动问候一次"
        return "突然想起你，来敲一下门。"

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fake_judge)
    monkeypatch.setattr(proactive_outreach, "generate_outreach_message", fake_generate)
    monkeypatch.setattr(proactive_outreach, "push_to_qq", fake_push_to_qq)

    result = await proactive_outreach.run_outreach_once(
        "superuser",
        db=db_session,
        now=now,
        max_silence_min=2880,
    )

    assert result["status"] == "sent"
    assert result["forced"] is True
    assert judge_calls == []
    assert pushes == [("private", "superuser", "突然想起你，来敲一下门。")]

    forced_row = (
        db_session.query(ProactiveOutreachLog)
        .filter_by(user_id="superuser", forced=True, status="sent")
        .one()
    )
    assert forced_row.message == "突然想起你，来敲一下门。"


@pytest.mark.asyncio
async def test_run_outreach_once_forces_message_when_never_sent_but_pending_is_stale(monkeypatch, db_session):
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

    pushes = []

    def fail_judge(*args, **kwargs):
        raise AssertionError("从未 sent 但 pending 已超过 max_silence 时应绕过 Judge")

    def fake_generate(grounding, reason):
        assert reason == "超过最长沉默窗口，主动问候一次"
        return "拖太久啦，忽然很想来问问你。"

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fail_judge)
    monkeypatch.setattr(proactive_outreach, "generate_outreach_message", fake_generate)
    monkeypatch.setattr(proactive_outreach, "push_to_qq", fake_push_to_qq)

    result = await proactive_outreach.run_outreach_once(
        "superuser",
        db=db_session,
        now=now,
        max_silence_min=2880,
    )

    assert result["status"] == "sent"
    assert result["forced"] is True
    assert pushes == [("private", "superuser", "拖太久啦，忽然很想来问问你。")]


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
        surge_min_prob=0.0,
        surge_max_prob=0.0,
        random_fn=lambda: 1.0,
    )

    assert result["status"] == "skipped_not_due"
    assert result["next_check_at"] == "2026-07-06T13:00:00"


@pytest.mark.asyncio
async def test_run_outreach_due_once_max_silence_overrides_future_next_check(monkeypatch, db_session):
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

    assert result == {"status": "sent", "forced": True}
    assert calls


@pytest.mark.asyncio
async def test_run_outreach_due_once_never_sent_stale_pending_overrides_future_next_check(monkeypatch, db_session):
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
        return {"status": "sent", "forced": True}

    monkeypatch.setattr(proactive_outreach, "run_outreach_once", fake_run_outreach_once)

    result = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=now,
        max_silence_min=2880,
    )

    assert result == {"status": "sent", "forced": True}
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
        }

    def fake_generate(grounding, reason):
        return f"生成：{reason}"

    async def fake_push_to_qq(target_type, target_id, message):
        pushes.append((target_type, target_id, message))
        return True

    monkeypatch.setattr(proactive_outreach, "judge_outreach", fake_judge)
    monkeypatch.setattr(proactive_outreach, "generate_outreach_message", fake_generate)
    monkeypatch.setattr(proactive_outreach, "push_to_qq", fake_push_to_qq)

    first = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=first_now,
        min_interval_min=0,
        max_silence_min=999999,
    )
    second = await proactive_outreach.run_outreach_due_once(
        "superuser",
        db=db_session,
        now=second_now,
        min_interval_min=0,
        max_silence_min=999999,
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
async def test_deliver_outreach_once_marks_sending_before_push(monkeypatch, db_session):
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

    monkeypatch.setattr(proactive_outreach, "push_to_qq", fake_push_to_qq)

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
    )

    assert result["status"] == "sent"
    assert observed_statuses == ["sending"]


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

    monkeypatch.setattr(proactive_outreach, "push_to_qq", fail_push_to_qq)

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
