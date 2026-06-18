import pytest


@pytest.mark.asyncio
async def test_group_message_continue_returns_standard_envelope(db_session, monkeypatch):
    from api.routes import GroupMessageRequest, group_message

    async def fake_process(*args, **kwargs):
        return {"action": "continue", "generation": 3, "reason": "reply now"}

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return "群聊标准回复"

        def pop_last_reply_meta(self, session_id):
            return {
                "send_mode": "quote",
                "reply_to_message_id": "m1",
                "_agent_result": "ok",
            }

    monkeypatch.setattr("api.routes.get_bridge", lambda: FakeBridge())
    monkeypatch.setattr("core.timing_runtime.GroupRuntime.process_message", fake_process)

    data = await group_message(
        GroupMessageRequest(
            group_id="envelope-group",
            sender_id="u-envelope",
            sender_name="信封测试",
            message="bot 你好",
            session_name="信封群",
            is_at_bot=True,
            client_meta={"platform": "web"},
            message_id="m-envelope-1",
        ),
        db_session,
        None,
    )

    assert data["action"] == "continue"
    assert data["status"] == "ok"
    assert data["reply"] == "群聊标准回复"
    assert data["messages"] == [{"type": "text", "text": "群聊标准回复"}]
    assert data["reply_meta"] == {
        "send_mode": "quote",
        "reply_to_message_id": "m1",
    }
    assert data["generation"] == 3
    assert data["reason"] == "reply now"
    assert data["meta"]["platform"] == "web"
    assert data["meta"]["chat_type"] == "group"
    assert data["meta"]["group_id"] == "envelope-group"
    assert data["meta"]["generation"] == 3


@pytest.mark.asyncio
async def test_group_message_wait_returns_empty_standard_envelope(db_session, monkeypatch):
    from api.routes import GroupMessageRequest, group_message

    async def fake_process(*args, **kwargs):
        return {
            "action": "wait",
            "generation": 5,
            "delay_seconds": 8,
            "reason": "user may type more",
        }

    monkeypatch.setattr("core.timing_runtime.GroupRuntime.process_message", fake_process)

    data = await group_message(
        GroupMessageRequest(
            group_id="wait-envelope",
            sender_id="u-wait",
            sender_name="等待测试",
            message="我想问一下",
            session_name="等待群",
            is_at_bot=True,
            client_meta={"platform": "qq"},
        ),
        db_session,
        None,
    )

    assert data["action"] == "wait"
    assert data["status"] == "wait"
    assert data["delay_seconds"] == 8
    assert data["generation"] == 5
    assert data["reply"] == ""
    assert data["messages"] == []
    assert data["reply_meta"] == {}
    assert data["meta"]["delay_seconds"] == 8


@pytest.mark.asyncio
async def test_group_message_prompt_audit_failure_keeps_standard_envelope(
    db_session, monkeypatch
):
    from api.routes import GroupMessageRequest, group_message

    class FakeBridge:
        async def handle_message(self, *args, **kwargs):
            return ""

        def pop_last_reply_meta(self, session_id):
            return {"_agent_result": "prompt_v2_audit_failed"}

    class FakeRuntime:
        async def process_message(self, *args, **kwargs):
            return {
                "action": "continue",
                "generation": 1,
                "reason": "audit failure path",
            }

        def note_bot_replied(self, *args, **kwargs):
            raise AssertionError("audit failure must not mark bot as replied")

    monkeypatch.setattr("api.routes.get_bridge", lambda: FakeBridge())
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: FakeRuntime())

    data = await group_message(
        GroupMessageRequest(
            group_id="audit-envelope",
            sender_id="u-audit-envelope",
            sender_name="审计信封",
            message="触发审计失败",
            session_name="审计信封群",
            is_at_bot=True,
            message_id="m-audit-envelope-1",
        ),
        db_session,
        None,
    )

    assert data["action"] == "no_reply"
    assert data["status"] == "no_reply"
    assert data["reply"] == ""
    assert data["messages"] == []
    assert data["reply_meta"] == {}
    assert data["reason"] == "prompt_v2_audit_failed"
    assert data["diagnostics"]["agent_result"] == "prompt_v2_audit_failed"
    assert data["meta"]["diagnostics"]["agent_result"] == "prompt_v2_audit_failed"
