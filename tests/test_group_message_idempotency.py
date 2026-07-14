from __future__ import annotations

import pytest


def _request(
    *,
    group_id: str,
    message_id: str | None,
    sender_id: str = "sender-current",
    **overrides,
):
    from api.group_message_routes import GroupMessageRequest

    values = {
        "group_id": group_id,
        "sender_id": sender_id,
        "sender_name": "幂等测试",
        "message": "hello",
        "message_id": message_id,
        "client_meta": {"platform": "qq", "chat_type": "group"},
    }
    values.update(overrides)
    return GroupMessageRequest(**values)


@pytest.mark.asyncio
async def test_canonical_group_ids_share_claim_and_replay_uses_current_identity(
    db_session,
    monkeypatch,
):
    from app.group_ingress import response_contract
    from app.group_ingress.service import GroupIngressResult, GroupIngressService
    from core.database import InboundMessageClaim
    from core.inbound_claim_lifecycle import InboundClaimOwner

    events: list[str] = []
    original_start = InboundClaimOwner.start

    async def recording_start(owner):
        events.append("start")
        return await original_start(owner)

    async def execute(
        req,
        *,
        group_user_id,
        attempt_count,
        claim_key,
        claim_owner,
    ):
        events.append("execute")
        assert group_user_id == "group_123"
        assert attempt_count == 1
        assert claim_key.session_id == "group_123"
        assert claim_owner.handle.key == claim_key
        completion = response_contract.build_completed_group_response(
            outcome="no_reply",
            reason="bot_sender:current_bot",
            generation=0,
            hard_rule="bot_sender_no_timing",
        )
        return GroupIngressResult(
            payload=response_contract.completed_group_response_payload(req, completion),
            completion=completion,
        )

    monkeypatch.setattr(InboundClaimOwner, "start", recording_start)
    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute, raising=False)

    first = await service.handle(
        _request(group_id="123", message_id=" canonical-message ", sender_id="sender-a")
    )

    assert events == ["start", "execute"]
    assert first["meta"]["group_id"] == "123"
    assert first["meta"]["message_id"] == "canonical-message"
    assert first["meta"]["sender_id"] == "sender-a"
    assert first["hard_rule"] == "bot_sender_no_timing"
    row = db_session.query(InboundMessageClaim).one()
    assert row.session_id == "group_123"
    assert row.message_id == "canonical-message"
    assert row.status == "completed"
    db_session.rollback()

    second = await service.handle(
        _request(
            group_id="qq:123:group",
            message_id="canonical-message",
            sender_id="sender-b",
        )
    )

    assert events == ["start", "execute"]
    assert second["action"] == "no_reply"
    assert second["reason"] == "bot_sender:current_bot"
    assert second["hard_rule"] == "bot_sender_no_timing"
    assert second["meta"]["group_id"] == "qq:123:group"
    assert second["meta"]["message_id"] == "canonical-message"
    assert second["meta"]["sender_id"] == "sender-b"
    assert db_session.query(InboundMessageClaim).count() == 1


@pytest.mark.asyncio
async def test_duplicate_inflight_returns_before_business_and_normalizes_message_id(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.inbound_idempotency import acquire_inbound_claim, normalize_inbound_claim_key

    key = normalize_inbound_claim_key("qq", "group", "group_42", "message-inflight")
    acquire_inbound_claim(db_session, key)

    async def forbidden_execute(*_args, **_kwargs):
        raise AssertionError("inflight 不应进入群聊业务体")

    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", forbidden_execute, raising=False)
    req = _request(group_id="42", message_id=" message-inflight ")

    payload = await service.handle(req)

    assert req.message_id == "message-inflight"
    assert payload["status"] == "duplicate_inflight"
    assert payload["action"] == "duplicate_inflight"
    assert payload["reply"] == ""
    assert payload["messages"] == []
    assert payload["reply_meta"] == {}
    assert payload["meta"]["group_id"] == "42"
    assert payload["meta"]["message_id"] == "message-inflight"


@pytest.mark.asyncio
async def test_timing_no_reply_business_result_completes_claim(
    db_session,
    monkeypatch,
):
    from app.group_ingress import response_contract
    from app.group_ingress.service import GroupIngressResult, GroupIngressService
    from core.database import InboundMessageClaim

    calls: list[tuple[str, int]] = []

    async def execute(
        req,
        *,
        group_user_id,
        attempt_count,
        claim_key,
        claim_owner,
    ):
        calls.append((group_user_id, attempt_count))
        assert claim_key.session_id == group_user_id
        assert claim_owner.handle.key == claim_key
        completion = response_contract.build_completed_group_response(
            outcome="no_reply",
            reason="timing says quiet",
            generation=3,
        )
        return GroupIngressResult(
            payload=response_contract.completed_group_response_payload(req, completion),
            completion=completion,
        )

    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute, raising=False)

    payload = await service.handle(_request(group_id="77", message_id="timing-no-reply"))

    assert payload["action"] == "no_reply"
    assert payload["reason"] == "timing says quiet"
    assert payload["generation"] == 3
    assert calls == [("group_77", 1)]
    row = db_session.query(InboundMessageClaim).one()
    assert row.status == "completed"
    assert "timing says quiet" in row.response_json
    assert '"payload"' not in row.response_json


@pytest.mark.parametrize(
    ("timing_result", "expected_outcome"),
    [
        (
            {
                "action": "no_reply",
                "generation": 4,
                "reason": "fallback quiet",
                "error_type": "network_error",
                "fallback_action": "no_reply",
            },
            "no_reply",
        ),
        (
            {
                "action": "wait",
                "generation": 5,
                "delay_seconds": 8,
                "reason": "wait for more",
            },
            "wait",
        ),
    ],
)
@pytest.mark.asyncio
async def test_real_timing_business_terminal_completes_and_replays_without_side_effects(
    db_session,
    monkeypatch,
    timing_result,
    expected_outcome,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, InboundMessageClaim
    from core.inbound_idempotency import decode_completed_inbound_response

    class FakeRuntime:
        def __init__(self):
            self.calls = 0

        async def process_message(self, *_args, **_kwargs):
            self.calls += 1
            return dict(timing_result)

    runtime = FakeRuntime()
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: runtime)
    service = GroupIngressService(
        db=db_session,
        bridge_provider=lambda: (_ for _ in ()).throw(
            AssertionError("Timing 业务终态不应进入 Bridge")
        ),
    )

    first = await service.handle(
        _request(group_id="timing-88", message_id="timing-business")
    )

    assert first["action"] == timing_result["action"]
    assert first["generation"] == timing_result["generation"]
    if timing_result["action"] == "wait":
        assert first["delay_seconds"] == 8
    row = db_session.query(InboundMessageClaim).one()
    assert row.status == "completed"
    completion = decode_completed_inbound_response(row.response_json)
    assert completion.outcome == expected_outcome
    assert completion.group.generation == timing_result["generation"]
    assert completion.group.delay_seconds == timing_result.get("delay_seconds")
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 1
    db_session.rollback()

    replay = await service.handle(
        _request(
            group_id="group_timing-88",
            message_id="timing-business",
            sender_id="replay-sender",
        )
    )

    assert replay["action"] == timing_result["action"]
    assert replay["meta"]["group_id"] == "group_timing-88"
    assert replay["meta"]["sender_id"] == "replay-sender"
    assert runtime.calls == 1
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 1


@pytest.mark.asyncio
async def test_current_bot_business_terminal_completes_with_hard_rule(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import decode_completed_inbound_response

    class ForbiddenRuntime:
        async def process_message(self, *_args, **_kwargs):
            raise AssertionError("current bot 不应进入 Timing")

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: ForbiddenRuntime())
    service = GroupIngressService(db=db_session)

    payload = await service.handle(
        _request(
            group_id="bot-99",
            message_id="bot-terminal",
            sender_id="bot-id",
            self_id="bot-id",
            bot_id="bot-id",
        )
    )

    assert payload["action"] == "no_reply"
    assert payload["generation"] == 0
    assert payload["hard_rule"] == "bot_sender_no_timing"
    assert "hard_rule" not in payload["meta"]
    row = db_session.query(InboundMessageClaim).one()
    completion = decode_completed_inbound_response(row.response_json)
    assert completion.outcome == "no_reply"
    assert completion.group.hard_rule == "bot_sender_no_timing"


@pytest.mark.parametrize(
    ("failure_point", "expected_reason", "error_marker"),
    [
        ("user", "db_locked:group_user_sync", "user lock exhausted"),
        ("ambient", "db_locked:ambient_log", "ambient lock exhausted"),
        ("timing", "error: timing technical boom", "timing technical boom"),
    ],
)
@pytest.mark.asyncio
async def test_technical_group_failure_returns_compatible_payload_and_fails_claim(
    db_session,
    monkeypatch,
    failure_point,
    expected_reason,
    error_marker,
):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            if failure_point == "timing":
                raise RuntimeError(error_marker)
            raise AssertionError(f"{failure_point} 失败后不应进入 Timing")

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    service = GroupIngressService(db=db_session)
    if failure_point in {"user", "ambient"}:
        locked_error = OperationalError(
            "COMMIT",
            {},
            sqlite3.OperationalError(f"database is locked: {error_marker}"),
        )

        def fail_with_lock(*_args, **_kwargs):
            raise locked_error

        target = "_sync_group_user" if failure_point == "user" else "_save_ambient_log"
        monkeypatch.setattr(service, target, fail_with_lock)

    payload = await service.handle(
        _request(
            group_id=f"technical-{failure_point}",
            message_id=f"technical-{failure_point}",
        )
    )

    assert payload["status"] == "no_reply"
    assert payload["action"] == "no_reply"
    assert payload["reason"] == expected_reason
    row = db_session.query(InboundMessageClaim).one()
    assert row.status == "failed"
    assert row.response_json == ""
    assert error_marker in row.error_summary


@pytest.mark.asyncio
async def test_bridge_reply_persists_raw_and_rebuilds_transport_on_live_and_replay(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, ConversationTurn, InboundMessageClaim
    from core.inbound_idempotency import decode_completed_inbound_response

    raw_answer = "raw [generated_image:bridge-test]"
    expansion_calls: list[tuple[str, bool]] = []

    def expand(answer, *, allow_base64):
        expansion_calls.append((answer, allow_base64))
        return f"expanded:{answer}"

    class Runtime:
        def __init__(self):
            self.calls = 0
            self.note_calls = 0

        async def process_message(self, *_args, **_kwargs):
            self.calls += 1
            return {
                "action": "continue",
                "generation": 6,
                "reason": "bridge now",
                "pending_text": "[用户名]幂等测试\n[发言内容]hello",
                "source_message_ids": ["bridge-message"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            self.note_calls += 1

    class Bridge:
        def __init__(self):
            self.calls = 0

        async def handle_message(self, *_args, **_kwargs):
            self.calls += 1
            return raw_answer

        def pop_last_reply_meta(self, _session_id):
            return {"send_mode": "quote", "_agent_result": "ok"}

    runtime = Runtime()
    bridge = Bridge()
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: runtime)
    monkeypatch.setattr(
        "core.generated_images.expand_generated_image_refs_in_content",
        expand,
    )
    service = GroupIngressService(db=db_session, bridge_provider=lambda: bridge)

    first = await service.handle(
        _request(group_id="bridge-100", message_id="bridge-message")
    )

    assert first["action"] == "continue"
    assert first["reply"] == f"expanded:{raw_answer}"
    assert first["reply_meta"] == {"send_mode": "quote"}
    assert runtime.calls == 1
    assert runtime.note_calls == 1
    assert bridge.calls == 1
    assistant_log = db_session.query(ChatLog).filter_by(role="assistant").one()
    assert assistant_log.content == raw_answer
    assistant_turn = db_session.query(ConversationTurn).filter_by(role="assistant").one()
    assert assistant_turn.content == raw_answer
    row = db_session.query(InboundMessageClaim).one()
    completion = decode_completed_inbound_response(row.response_json)
    assert completion.outcome == "respond"
    assert completion.reply == raw_answer
    db_session.rollback()

    replay = await service.handle(
        _request(
            group_id="group_bridge-100",
            message_id="bridge-message",
            sender_id="replay-sender",
        )
    )

    assert replay["reply"] == f"expanded:{raw_answer}"
    assert replay["meta"]["group_id"] == "group_bridge-100"
    assert replay["meta"]["sender_id"] == "replay-sender"
    assert runtime.calls == 1
    assert runtime.note_calls == 1
    assert bridge.calls == 1
    assert expansion_calls == [(raw_answer, False), (raw_answer, False)]


@pytest.mark.parametrize(
    "agent_result",
    [
        "no_reply_tool",
        "fake_tool_call_claim",
        "structured_buffer_reply",
        "structured_buffer_no_reply",
        "no_tool_call",
    ],
)
@pytest.mark.asyncio
async def test_legal_empty_bridge_reply_completes_as_no_reply(
    db_session,
    monkeypatch,
    agent_result,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, InboundMessageClaim
    from core.inbound_idempotency import decode_completed_inbound_response

    class Runtime:
        def __init__(self):
            self.note_calls = 0

        async def process_message(self, *_args, **_kwargs):
            return {
                "action": "continue",
                "generation": 2,
                "reason": "bridge requested",
                "pending_text": "hello",
                "source_message_ids": ["empty-bridge"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            self.note_calls += 1

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            return ""

        def pop_last_reply_meta(self, _session_id):
            return {"_agent_result": agent_result}

    runtime = Runtime()
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: runtime)
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())

    payload = await service.handle(
        _request(group_id="empty-bridge", message_id="empty-bridge")
    )

    assert payload["status"] == "no_reply"
    assert payload["action"] == "no_reply"
    assert payload["reply"] == ""
    assert payload["messages"] == []
    assert payload["reason"] == agent_result
    assert runtime.note_calls == 0
    system_log = db_session.query(ChatLog).filter_by(role="system").one()
    assert f"agent_result={agent_result}" in system_log.content
    row = db_session.query(InboundMessageClaim).one()
    completion = decode_completed_inbound_response(row.response_json)
    assert completion.outcome == "no_reply"
    assert completion.reason == agent_result


@pytest.mark.parametrize(
    "failure_point",
    ["provider", "query", "bridge", "persist", "note"],
)
@pytest.mark.asyncio
async def test_bridge_technical_exception_fails_claim_with_original_error(
    db_session,
    monkeypatch,
    failure_point,
):
    from app.group_ingress import helpers as group_helpers
    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim

    marker = f"{failure_point} technical boom"
    technical_error = RuntimeError(marker)

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            return {
                "action": "continue",
                "generation": 3,
                "reason": "bridge requested",
                "pending_text": "hello",
                "source_message_ids": [f"bridge-tech-{failure_point}"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            if failure_point == "note":
                raise technical_error

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            if failure_point == "bridge":
                raise technical_error
            return "bridge answer"

        def pop_last_reply_meta(self, _session_id):
            return {}

    runtime = Runtime()
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: runtime)

    def bridge_provider():
        if failure_point == "provider":
            raise technical_error
        return Bridge()

    service = GroupIngressService(db=db_session, bridge_provider=bridge_provider)
    if failure_point == "query":
        monkeypatch.setattr(
            service,
            "_collect_bridge_files",
            lambda **_kwargs: (_ for _ in ()).throw(technical_error),
        )
    if failure_point == "persist":
        monkeypatch.setattr(
            group_helpers,
            "persist_group_bridge_reply",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(technical_error),
        )

    payload = await service.handle(
        _request(
            group_id=f"bridge-tech-{failure_point}",
            message_id=f"bridge-tech-{failure_point}",
        )
    )

    assert payload["status"] == "no_reply"
    assert payload["action"] == "no_reply"
    assert marker in payload["reason"]
    row = db_session.query(InboundMessageClaim).one()
    assert row.status == "failed"
    assert row.response_json == ""
    assert marker in row.error_summary


@pytest.mark.asyncio
async def test_prompt_audit_failure_returns_diagnostics_but_fails_claim(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            return {
                "action": "continue",
                "generation": 9,
                "reason": "bridge requested",
                "pending_text": "hello",
                "source_message_ids": ["prompt-audit"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            raise AssertionError("audit failure 不应标记 bot replied")

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            return ""

        def pop_last_reply_meta(self, _session_id):
            return {"_agent_result": "prompt_v2_audit_failed"}

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())

    payload = await service.handle(
        _request(group_id="prompt-audit", message_id="prompt-audit")
    )

    assert payload["status"] == "no_reply"
    assert payload["action"] == "no_reply"
    assert payload["generation"] == 9
    assert payload["reason"] == "prompt_v2_audit_failed"
    assert payload["diagnostics"] == {
        "timing_action": "continue",
        "agent_result": "prompt_v2_audit_failed",
    }
    assert payload["meta"]["diagnostics"] == payload["diagnostics"]
    row = db_session.query(InboundMessageClaim).one()
    assert row.status == "failed"
    assert row.response_json == ""
    assert "prompt_v2_audit_failed" in row.error_summary


@pytest.mark.asyncio
async def test_failed_takeover_reuses_ambient_with_inbound_request_fingerprint(
    db_session,
    monkeypatch,
):
    import json

    from app.group_ingress import helpers as group_helpers
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, InboundMessageClaim

    counts = {"stickers": 0, "user": 0, "precache": 0, "ambient": 0}
    recent_contexts: list[str] = []
    timing_error = RuntimeError("first timing failure")

    class Runtime:
        def __init__(self):
            self.calls = 0

        async def process_message(self, *_args, **kwargs):
            self.calls += 1
            recent_contexts.append(str(kwargs.get("recent_context") or ""))
            if self.calls == 1:
                raise timing_error
            return {
                "action": "no_reply",
                "generation": 1,
                "reason": "recovered after takeover",
            }

    runtime = Runtime()
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: runtime)
    original_register = group_helpers.register_group_stickers_from_message

    def recording_register(*args, **kwargs):
        counts["stickers"] += 1
        return original_register(*args, **kwargs)

    monkeypatch.setattr(
        group_helpers,
        "register_group_stickers_from_message",
        recording_register,
    )
    service = GroupIngressService(db=db_session)
    original_sync = service._sync_group_user
    original_precache = service._schedule_image_precache
    original_save = service._save_ambient_log

    def recording_sync(*args, **kwargs):
        counts["user"] += 1
        return original_sync(*args, **kwargs)

    def recording_precache(*args, **kwargs):
        counts["precache"] += 1
        return original_precache(*args, **kwargs)

    def recording_save(*args, **kwargs):
        counts["ambient"] += 1
        return original_save(*args, **kwargs)

    monkeypatch.setattr(service, "_sync_group_user", recording_sync)
    monkeypatch.setattr(service, "_schedule_image_precache", recording_precache)
    monkeypatch.setattr(service, "_save_ambient_log", recording_save)

    first = await service.handle(
        _request(
            group_id="takeover-ambient",
            message_id="takeover-ambient",
            message="recovery marker",
        )
    )

    assert first["action"] == "no_reply"
    assert "first timing failure" in first["reason"]
    first_claim = db_session.query(InboundMessageClaim).one()
    assert first_claim.status == "failed"
    assert first_claim.attempt_count == 1
    ambient = db_session.query(ChatLog).filter_by(role="ambient").one()
    ambient_meta = json.loads(ambient.meta_json)
    assert ambient_meta["inbound_request"]["schema_version"] == 1
    assert (
        ambient_meta["inbound_request"]["canonicalizer"]
        == "group-business-input-v1"
    )
    assert len(ambient_meta["inbound_request"]["sha256"]) == 64
    db_session.rollback()

    second = await service.handle(
        _request(
            group_id="group_takeover-ambient",
            message_id="takeover-ambient",
            message="recovery marker",
        )
    )

    assert second["action"] == "no_reply"
    assert second["reason"] == "recovered after takeover"
    assert runtime.calls == 2
    assert counts == {"stickers": 1, "user": 1, "precache": 1, "ambient": 1}
    assert "recovery marker" not in recent_contexts[1]
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 1
    recovered_claim = db_session.query(InboundMessageClaim).one()
    assert recovered_claim.status == "completed"
    assert recovered_claim.attempt_count == 2


@pytest.mark.asyncio
async def test_bridge_resolver_failure_recovers_without_duplicate_group_effects(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, InboundMessageClaim

    resolver_error = RuntimeError("session guidance resolver failed")
    calls = {
        "bridge": 0,
        "model": 0,
        "timing": 0,
        "bot_replied": 0,
    }

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            calls["timing"] += 1
            return {
                "action": "continue",
                "generation": 1,
                "reason": "resolver recovery",
                "pending_text": "resolver recovery marker",
                "source_message_ids": ["resolver-recovery"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            calls["bot_replied"] += 1

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            calls["bridge"] += 1
            if calls["bridge"] == 1:
                raise resolver_error
            calls["model"] += 1
            return "恢复后的群聊回复"

        def pop_last_reply_meta(self, _session_id):
            return {}

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())

    first = await service.handle(
        _request(
            group_id="resolver-recovery",
            message_id="resolver-recovery",
            message="resolver recovery marker",
            is_at_bot=True,
        )
    )

    assert first["status"] == "no_reply"
    assert first["action"] == "no_reply"
    assert first["reply"] == ""
    assert first["messages"] == []
    assert "session guidance resolver failed" in first["reason"]
    failed_claim = db_session.query(InboundMessageClaim).one()
    assert failed_claim.status == "failed"
    assert failed_claim.response_json == ""
    assert failed_claim.attempt_count == 1
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 1
    assert db_session.query(ChatLog).filter_by(role="assistant").count() == 0
    db_session.rollback()

    recovered = await service.handle(
        _request(
            group_id="group_resolver-recovery",
            message_id="resolver-recovery",
            message="resolver recovery marker",
            is_at_bot=True,
        )
    )

    assert recovered["action"] == "continue"
    assert recovered["reply"] == "恢复后的群聊回复"
    assert calls == {
        "bridge": 2,
        "model": 1,
        "timing": 2,
        "bot_replied": 1,
    }
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 1
    assert db_session.query(ChatLog).filter_by(role="assistant").count() == 1
    completed_claim = db_session.query(InboundMessageClaim).one()
    assert completed_claim.status == "completed"
    assert completed_claim.attempt_count == 2


@pytest.mark.parametrize(
    ("changed_fields", "case_name"),
    [
        ({"sender_id": "sender-b"}, "sender"),
        ({"message": "被替换的正文"}, "message"),
        (
            {
                "segments": [
                    {"type": "text", "data": {"text": "被替换的消息段"}}
                ]
            },
            "segments",
        ),
        (
            {
                "reply_to": {
                    "message_id": "quoted-message",
                    "sender_id": "quoted-user",
                    "content": "quoted",
                }
            },
            "reply-target",
        ),
        ({"is_at_bot": True}, "direction"),
        ({"files": ["img://changed"]}, "files"),
        ({"bot_id": "bot-changed"}, "bot-identity"),
        ({"bot_aliases": ["另一个名字"]}, "bot-aliases"),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
@pytest.mark.asyncio
async def test_failed_takeover_rejects_changed_inbound_request_before_timing(
    db_session,
    monkeypatch,
    changed_fields,
    case_name,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim

    timing_calls = 0
    bridge_calls = 0
    first_error = RuntimeError(f"first timing failure: {case_name}")

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            nonlocal timing_calls
            timing_calls += 1
            raise first_error

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            nonlocal bridge_calls
            bridge_calls += 1
            return "不应产生的回复"

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())
    message_id = f"input-mismatch-{case_name}"
    base_fields = {
        "sender_id": "sender-a",
        "message": "原正文",
    }

    first = await service.handle(
        _request(
            group_id=f"input-mismatch-{case_name}",
            message_id=message_id,
            **base_fields,
        )
    )
    assert "first timing failure" in first["reason"]
    db_session.rollback()

    second = await service.handle(
        _request(
            group_id=f"group_input-mismatch-{case_name}",
            message_id=message_id,
            **{**base_fields, **changed_fields},
        )
    )

    assert second["action"] == "no_reply"
    assert second["reason"] == "inbound_request_mismatch"
    assert timing_calls == 1
    assert bridge_calls == 0
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "failed"
    assert claim.attempt_count == 2


@pytest.mark.asyncio
async def test_failed_takeover_without_ambient_reruns_full_preflow(
    db_session,
    monkeypatch,
):
    import sqlite3

    from sqlalchemy.exc import OperationalError

    from app.group_ingress import helpers as group_helpers
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, InboundMessageClaim

    counts = {"stickers": 0, "user": 0, "ambient": 0, "timing": 0}
    locked_error = OperationalError(
        "COMMIT",
        {},
        sqlite3.OperationalError("database is locked: before ambient"),
    )

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            counts["timing"] += 1
            return {
                "action": "no_reply",
                "generation": 1,
                "reason": "full preflow recovered",
            }

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    original_register = group_helpers.register_group_stickers_from_message

    def recording_register(*args, **kwargs):
        counts["stickers"] += 1
        return original_register(*args, **kwargs)

    monkeypatch.setattr(
        group_helpers,
        "register_group_stickers_from_message",
        recording_register,
    )
    service = GroupIngressService(db=db_session)
    original_sync = service._sync_group_user
    original_save = service._save_ambient_log

    def flaky_sync(*args, **kwargs):
        counts["user"] += 1
        if counts["user"] == 1:
            raise locked_error
        return original_sync(*args, **kwargs)

    def recording_save(*args, **kwargs):
        counts["ambient"] += 1
        return original_save(*args, **kwargs)

    monkeypatch.setattr(service, "_sync_group_user", flaky_sync)
    monkeypatch.setattr(service, "_save_ambient_log", recording_save)
    req = _request(group_id="takeover-no-ambient", message_id="takeover-no-ambient")

    first = await service.handle(req)

    assert first["reason"] == "db_locked:group_user_sync"
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 0
    db_session.rollback()

    second = await service.handle(
        _request(group_id="group_takeover-no-ambient", message_id="takeover-no-ambient")
    )

    assert second["reason"] == "full preflow recovered"
    assert counts == {"stickers": 2, "user": 2, "ambient": 1, "timing": 1}
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 1
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "completed"
    assert claim.attempt_count == 2


@pytest.mark.asyncio
async def test_first_attempt_with_legacy_ambient_completes_duplicate_without_preflow(
    db_session,
    monkeypatch,
):
    import json

    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, InboundMessageClaim
    from core.inbound_idempotency import decode_completed_inbound_response

    db_session.add(
        ChatLog(
            user_id="group_legacy-ambient",
            session_id="group_legacy-ambient",
            sender_name="历史用户",
            role="ambient",
            content="[历史用户]: old",
            processed=1,
            message_id="legacy-ambient",
            meta_json=json.dumps({"message_type": "group_message"}),
        )
    )
    db_session.commit()

    async def forbidden_timing(*_args, **_kwargs):
        raise AssertionError("legacy ambient 不应进入 Timing")

    class ForbiddenRuntime:
        process_message = forbidden_timing

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: ForbiddenRuntime())
    monkeypatch.setattr(
        "app.group_ingress.helpers.register_group_stickers_from_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy ambient 不应重跑 sticker 前置")
        ),
    )
    service = GroupIngressService(db=db_session)

    payload = await service.handle(
        _request(group_id="legacy-ambient", message_id="legacy-ambient")
    )

    assert payload["action"] == "no_reply"
    assert payload["reason"] == "duplicate_message"
    assert db_session.query(ChatLog).filter_by(role="ambient").count() == 1
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "completed"
    assert claim.attempt_count == 1
    completion = decode_completed_inbound_response(claim.response_json)
    assert completion.outcome == "no_reply"
    assert completion.reason == "duplicate_message"


@pytest.mark.asyncio
async def test_sticker_preview_await_releases_clean_request_transaction(
    db_session,
    monkeypatch,
):
    from sqlalchemy import text

    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim

    observed_transactions: list[bool] = []

    def register_with_refresh_transaction(db, *_args, **_kwargs):
        db.execute(text("SELECT 1"))
        assert db.in_transaction()
        return [{"id": 101}]

    async def inspect_before_preview(_registered_stickers):
        observed_transactions.append(db_session.in_transaction())
        assert not db_session.in_transaction()

    monkeypatch.setattr(
        "app.group_ingress.helpers.register_group_stickers_from_message",
        register_with_refresh_transaction,
    )
    service = GroupIngressService(db=db_session, background_tasks=None)
    monkeypatch.setattr(service, "_cache_registered_sticker_previews", inspect_before_preview)

    payload = await service.handle(
        _request(
            group_id="sticker-await",
            message_id="sticker-await",
            sender_id="bot-id",
            self_id="bot-id",
            bot_id="bot-id",
        )
    )

    assert observed_transactions == [False]
    assert payload["action"] == "no_reply"
    assert db_session.query(InboundMessageClaim).one().status == "completed"


@pytest.mark.parametrize("blocked_kind", ["user", "content"])
@pytest.mark.asyncio
async def test_blocked_business_terminal_persists_blocked_outcome_with_no_reply_http(
    db_session,
    monkeypatch,
    blocked_kind,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import decode_completed_inbound_response

    monkeypatch.setattr(
        "app.group_ingress.helpers.check_user_blocked",
        lambda *_args, **_kwargs: blocked_kind == "user",
    )
    if blocked_kind == "content":
        monkeypatch.setattr(
            "app.group_ingress.service.check_message_moderation_db",
            lambda *_args, **_kwargs: {
                "pattern": "blocked",
                "rule_id": 1,
                "category": "test",
                "match_type": "contains",
                "scope_type": "group",
                "reason": "test",
                "no_reply": True,
                "no_learn": False,
                "no_context": False,
            },
        )

    class ForbiddenRuntime:
        async def process_message(self, *_args, **_kwargs):
            raise AssertionError("blocked 业务终态不应进入 Timing")

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: ForbiddenRuntime())
    service = GroupIngressService(db=db_session)

    payload = await service.handle(
        _request(
            group_id=f"blocked-{blocked_kind}",
            message_id=f"blocked-{blocked_kind}",
        )
    )

    assert payload["status"] == "no_reply"
    assert payload["action"] == "no_reply"
    assert payload["generation"] == 0
    assert payload["reason"] == f"{blocked_kind}_blocked"
    claim = db_session.query(InboundMessageClaim).one()
    completion = decode_completed_inbound_response(claim.response_json)
    assert completion.outcome == "blocked"
    assert completion.reason == f"{blocked_kind}_blocked"


@pytest.mark.asyncio
async def test_duplicate_reply_suppression_completes_with_replay_fields(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim
    from core.inbound_idempotency import decode_completed_inbound_response

    duplicate = {
        "previous_log_id": 12,
        "similarity": 0.96,
        "previous_created_at": "2026-07-10T17:00:00",
    }

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            return {
                "action": "continue",
                "generation": 4,
                "reason": "bridge requested",
                "pending_text": "hello",
                "source_message_ids": ["duplicate-reply"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            raise AssertionError("重复回复抑制后不应标记 bot replied")

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            return "重复回答"

        def pop_last_reply_meta(self, _session_id):
            return {}

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    monkeypatch.setattr(
        "app.group_ingress.helpers.find_recent_duplicate_group_reply",
        lambda *_args, **_kwargs: dict(duplicate),
    )
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())

    payload = await service.handle(
        _request(group_id="duplicate-reply", message_id="duplicate-reply")
    )

    assert payload["action"] == "no_reply"
    assert payload["reason"] == "duplicate_reply_suppressed"
    assert payload["duplicate_reply"] == duplicate
    assert payload["meta"]["duplicate_reply"] == duplicate
    claim = db_session.query(InboundMessageClaim).one()
    completion = decode_completed_inbound_response(claim.response_json)
    assert completion.outcome == "no_reply"
    assert completion.group.duplicate_reply == duplicate


@pytest.mark.asyncio
async def test_blank_message_id_bypasses_claim(
    db_session,
    monkeypatch,
):
    from app.group_ingress import response_contract
    from app.group_ingress.service import GroupIngressResult, GroupIngressService
    from core.database import InboundMessageClaim

    calls: list[tuple[str, int]] = []

    async def execute(
        req,
        *,
        group_user_id,
        attempt_count,
        claim_key,
        claim_owner,
    ):
        calls.append((group_user_id, attempt_count))
        assert claim_key is None
        assert claim_owner is None
        completion = response_contract.build_completed_group_response(
            outcome="no_reply",
            reason="blank bypass",
        )
        return GroupIngressResult(
            payload=response_contract.completed_group_response_payload(req, completion),
            completion=completion,
        )

    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute)

    payload = await service.handle(_request(group_id="blank-bypass", message_id="  "))

    assert payload["action"] == "no_reply"
    assert payload["reason"] == "blank bypass"
    assert calls == [("group_blank-bypass", 0)]
    assert db_session.query(InboundMessageClaim).count() == 0


@pytest.mark.asyncio
async def test_owner_loss_before_bridge_cancels_request_without_bridge_or_persistence(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    checkpoint_error = InboundClaimOwnershipLostError("lost before bridge")
    bridge_calls = 0
    checkpoint_calls = 0

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            return {
                "action": "continue",
                "generation": 1,
                "reason": "checkpoint before bridge",
                "pending_text": "hello",
                "source_message_ids": ["lost-before-bridge"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            raise AssertionError("失权请求不应标记 bot replied")

    async def fail_checkpoint(_owner):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        raise checkpoint_error

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            nonlocal bridge_calls
            bridge_calls += 1
            return "禁止产生的回复"

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    monkeypatch.setattr(InboundClaimOwner, "checkpoint", fail_checkpoint)
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())

    with pytest.raises(InboundClaimOwnershipLostError) as raised:
        await service.handle(
            _request(
                group_id="lost-before-bridge",
                message_id="lost-before-bridge",
                is_at_bot=True,
            )
        )

    assert raised.value is checkpoint_error
    assert checkpoint_calls == 1
    assert bridge_calls == 0
    assert db_session.query(ChatLog).filter_by(role="assistant").count() == 0


@pytest.mark.asyncio
async def test_owner_loss_after_bridge_prevents_reply_persistence(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    checkpoint_calls = 0
    lost = InboundClaimOwnershipLostError("lost after bridge")

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            return {
                "action": "continue",
                "generation": 2,
                "reason": "checkpoint after bridge",
                "pending_text": "hello",
                "source_message_ids": ["lost-after-bridge"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            raise AssertionError("失权请求不应标记 bot replied")

    async def checkpoint(_owner):
        nonlocal checkpoint_calls
        checkpoint_calls += 1
        if checkpoint_calls == 2:
            raise lost
        return True

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            return "已返回但不得落库"

        def pop_last_reply_meta(self, _session_id):
            return {}

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    monkeypatch.setattr(InboundClaimOwner, "checkpoint", checkpoint)
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())

    with pytest.raises(InboundClaimOwnershipLostError) as raised:
        await service.handle(
            _request(
                group_id="lost-after-bridge",
                message_id="lost-after-bridge",
                is_at_bot=True,
            )
        )

    assert raised.value is lost
    assert checkpoint_calls == 2
    assert db_session.query(ChatLog).filter_by(role="assistant").count() == 0


@pytest.mark.asyncio
async def test_owner_unusable_signal_cancels_group_business_task(
    db_session,
    monkeypatch,
):
    import asyncio

    from app.group_ingress.service import GroupIngressService
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    entered = asyncio.Event()
    cancelled = asyncio.Event()
    lost = InboundClaimOwnershipLostError("renew owner lost")

    async def execute(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def wait_unusable(_owner):
        await entered.wait()
        raise lost

    monkeypatch.setattr(InboundClaimOwner, "wait_unusable", wait_unusable)
    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute)

    task = asyncio.create_task(
        service.handle(_request(group_id="signal-loss", message_id="signal-loss"))
    )
    try:
        with pytest.raises(InboundClaimOwnershipLostError) as raised:
            await asyncio.wait_for(task, timeout=1)
        assert raised.value is lost
        assert cancelled.is_set()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_owner_unusable_signal_cancels_real_bridge_task(
    db_session,
    monkeypatch,
):
    import asyncio

    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    bridge_entered = asyncio.Event()
    bridge_cancelled = asyncio.Event()
    lost = InboundClaimOwnershipLostError("lost during bridge")

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            return {
                "action": "continue",
                "generation": 3,
                "reason": "cancel real bridge",
                "pending_text": "hello",
                "source_message_ids": ["loss-during-bridge"],
            }

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            bridge_entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                bridge_cancelled.set()
                raise

    async def wait_unusable(_owner):
        await bridge_entered.wait()
        raise lost

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    monkeypatch.setattr(InboundClaimOwner, "wait_unusable", wait_unusable)
    service = GroupIngressService(db=db_session, bridge_provider=lambda: Bridge())
    task = asyncio.create_task(
        service.handle(
            _request(
                group_id="loss-during-bridge",
                message_id="loss-during-bridge",
                is_at_bot=True,
            )
        )
    )
    try:
        with pytest.raises(InboundClaimOwnershipLostError) as raised:
            await asyncio.wait_for(task, timeout=1)
        assert raised.value is lost
        assert bridge_cancelled.is_set()
        assert db_session.query(ChatLog).filter_by(role="assistant").count() == 0
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize(
    "complete_error",
    [
        RuntimeError("group complete failed"),
        pytest.param(
            None,
            id="ownership-lost",
        ),
    ],
)
@pytest.mark.asyncio
async def test_owner_complete_error_never_returns_business_success(
    db_session,
    monkeypatch,
    complete_error,
):
    from app.group_ingress import response_contract
    from app.group_ingress.service import GroupIngressResult, GroupIngressService
    from core.database import InboundMessageClaim
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    if complete_error is None:
        complete_error = InboundClaimOwnershipLostError("group owner lost")

    async def execute(req, **_kwargs):
        completion = response_contract.build_completed_group_response(
            outcome="no_reply",
            reason="must not return",
        )
        return GroupIngressResult(
            payload=response_contract.completed_group_response_payload(req, completion),
            completion=completion,
        )

    async def fail_complete(_owner, _completion):
        raise complete_error

    monkeypatch.setattr(InboundClaimOwner, "complete", fail_complete)
    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute)

    with pytest.raises(type(complete_error)) as raised:
        await service.handle(
            _request(group_id="complete-error", message_id="complete-error")
        )

    assert raised.value is complete_error
    assert db_session.query(InboundMessageClaim).one().status == "failed"


@pytest.mark.asyncio
async def test_owner_complete_false_never_returns_business_success(
    db_session,
    monkeypatch,
):
    from app.group_ingress import response_contract
    from app.group_ingress.service import GroupIngressResult, GroupIngressService
    from core.database import InboundMessageClaim
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    async def execute(req, **_kwargs):
        completion = response_contract.build_completed_group_response(
            outcome="no_reply",
            reason="false complete 不得返回",
        )
        return GroupIngressResult(
            payload=response_contract.completed_group_response_payload(
                req,
                completion,
            ),
            completion=completion,
        )

    async def false_complete(owner, _completion):
        await owner._stop_renewal()
        return False

    monkeypatch.setattr(InboundClaimOwner, "complete", false_complete)
    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute)

    with pytest.raises(InboundClaimOwnershipLostError):
        await service.handle(
            _request(group_id="complete-false", message_id="complete-false")
        )

    assert db_session.query(InboundMessageClaim).one().status == "failed"


@pytest.mark.asyncio
async def test_technical_error_remains_primary_when_owner_fail_returns_false(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressResult, GroupIngressService
    from core.inbound_claim_lifecycle import (
        InboundClaimOwner,
        InboundClaimOwnershipLostError,
    )

    technical_error = RuntimeError("technical result")

    async def execute(req, **_kwargs):
        return GroupIngressResult(
            payload={
                "status": "no_reply",
                "action": "no_reply",
                "reply": "",
                "messages": [],
                "reply_meta": {},
                "meta": {},
            },
            technical_error=technical_error,
        )

    async def lose_owner(owner, _error):
        await owner._stop_renewal()
        return False

    monkeypatch.setattr(InboundClaimOwner, "fail", lose_owner)
    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute)

    with pytest.raises(RuntimeError) as raised:
        await service.handle(
            _request(group_id="fail-false", message_id="fail-false")
        )

    assert raised.value is technical_error
    secondary_errors = {raised.value.__cause__, raised.value.__context__}
    assert any(
        isinstance(error, InboundClaimOwnershipLostError)
        for error in secondary_errors
    )


@pytest.mark.asyncio
async def test_technical_error_remains_primary_when_owner_fail_raises(
    db_session,
    monkeypatch,
):
    from app.group_ingress.service import GroupIngressResult, GroupIngressService
    from core.inbound_claim_lifecycle import InboundClaimOwner

    primary = RuntimeError("timing primary")
    settlement = RuntimeError("claim fail settlement")
    fail_calls: list[BaseException] = []

    async def execute(_req, **_kwargs):
        return GroupIngressResult(
            payload={"action": "no_reply"},
            technical_error=primary,
        )

    async def fail_owner(owner, error):
        fail_calls.append(error)
        await owner._stop_renewal()
        raise settlement

    monkeypatch.setattr(InboundClaimOwner, "fail", fail_owner)
    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute)

    with pytest.raises(RuntimeError) as raised:
        await service.handle(
            _request(group_id="error-chain", message_id="error-chain")
        )

    assert raised.value is primary
    assert settlement in {raised.value.__cause__, raised.value.__context__}
    assert fail_calls == [primary]


@pytest.mark.parametrize("error_kind", ["cancel", "base_exception"])
@pytest.mark.asyncio
async def test_unhandled_cancel_or_base_exception_fails_claim_and_reraises_original(
    db_session,
    monkeypatch,
    error_kind,
):
    import asyncio

    from app.group_ingress.service import GroupIngressService
    from core.database import InboundMessageClaim

    class GroupFatalError(BaseException):
        pass

    error = (
        asyncio.CancelledError("group cancelled")
        if error_kind == "cancel"
        else GroupFatalError("group fatal")
    )

    async def execute(*_args, **_kwargs):
        raise error

    service = GroupIngressService(db=db_session)
    monkeypatch.setattr(service, "_execute_request", execute)

    with pytest.raises(type(error)) as raised:
        await service.handle(
            _request(
                group_id=f"fatal-{error_kind}",
                message_id=f"fatal-{error_kind}",
            )
        )

    assert raised.value is error
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "failed"
    assert claim.response_json == ""
    assert str(error) in claim.error_summary


@pytest.mark.asyncio
async def test_concurrent_canonical_group_ids_execute_timing_and_bridge_once(
    tmp_path,
    monkeypatch,
):
    import asyncio
    from contextlib import ExitStack

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.group_ingress.service import GroupIngressService
    from core import database
    from core.database import Base, ChatLog, InboundMessageClaim
    from core.inbound_claim_lifecycle import InboundClaimOwner

    timing_entered = asyncio.Event()
    release_timing = asyncio.Event()
    complete_calls: list[str] = []
    owner_session_ids: list[int] = []

    class Runtime:
        def __init__(self):
            self.calls = 0
            self.note_calls = 0

        async def process_message(self, *_args, **_kwargs):
            self.calls += 1
            timing_entered.set()
            await release_timing.wait()
            return {
                "action": "continue",
                "generation": 1,
                "reason": "concurrent owner",
                "pending_text": "hello",
                "source_message_ids": ["concurrent-message"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            self.note_calls += 1

    class Bridge:
        def __init__(self):
            self.calls = 0

        async def handle_message(self, *_args, **_kwargs):
            self.calls += 1
            return "并发唯一回复"

        def pop_last_reply_meta(self, _session_id):
            return {}

    runtime = Runtime()
    bridge = Bridge()
    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: runtime)
    original_fresh_call = InboundClaimOwner._call_with_fresh_session
    original_complete = InboundClaimOwner.complete

    def recording_fresh_call(owner, operation):
        def recording_operation(session):
            owner_session_ids.append(id(session))
            return operation(session)

        return original_fresh_call(owner, recording_operation)

    async def recording_complete(owner, completion):
        complete_calls.append(completion.outcome)
        return await original_complete(owner, completion)

    with ExitStack() as stack:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'group-concurrency.db'}",
            connect_args={"check_same_thread": False, "timeout": 1.0},
        )
        stack.callback(engine.dispose)
        Base.metadata.create_all(bind=engine)
        stack.callback(Base.metadata.drop_all, bind=engine)
        FileSessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        )
        monkeypatch.setattr(database, "SessionLocal", FileSessionLocal)
        monkeypatch.setattr(
            InboundClaimOwner,
            "_call_with_fresh_session",
            recording_fresh_call,
        )
        monkeypatch.setattr(InboundClaimOwner, "complete", recording_complete)
        db_a = stack.enter_context(FileSessionLocal())
        db_b = stack.enter_context(FileSessionLocal())
        db_replay = stack.enter_context(FileSessionLocal())
        inspect_db = stack.enter_context(FileSessionLocal())
        connection_a = db_a.connection()
        connection_b = db_b.connection()
        dbapi_a = connection_a.connection.dbapi_connection
        dbapi_b = connection_b.connection.dbapi_connection
        assert db_a is not db_b
        assert dbapi_a is not dbapi_b
        first_task = None
        try:
            service_a = GroupIngressService(db=db_a, bridge_provider=lambda: bridge)
            service_b = GroupIngressService(db=db_b, bridge_provider=lambda: bridge)
            first_task = asyncio.create_task(
                service_a.handle(
                    _request(
                        group_id="555",
                        message_id="concurrent-message",
                        sender_id="owner-sender",
                    )
                )
            )
            await asyncio.wait_for(timing_entered.wait(), timeout=5)

            duplicate = await service_b.handle(
                _request(
                    group_id="qq:555:group",
                    message_id="concurrent-message",
                    sender_id="duplicate-sender",
                )
            )

            assert duplicate["status"] == "duplicate_inflight"
            assert duplicate["action"] == "duplicate_inflight"
            assert duplicate["meta"]["group_id"] == "qq:555:group"
            assert duplicate["meta"]["sender_id"] == "duplicate-sender"
            assert runtime.calls == 1
            assert bridge.calls == 0

            release_timing.set()
            first = await asyncio.wait_for(first_task, timeout=5)

            assert first["action"] == "continue"
            assert first["reply"] == "并发唯一回复"
            assert runtime.calls == 1
            assert runtime.note_calls == 1
            assert bridge.calls == 1
            assert complete_calls == ["respond"]
            assert len(owner_session_ids) == 3
            assert all(
                session_id not in {id(db_a), id(db_b)}
                for session_id in owner_session_ids
            )

            replay = await GroupIngressService(
                db=db_replay,
                bridge_provider=lambda: bridge,
            ).handle(
                _request(
                    group_id="group_555",
                    message_id="concurrent-message",
                    sender_id="replay-sender",
                )
            )

            assert replay["action"] == "continue"
            assert replay["reply"] == "并发唯一回复"
            assert replay["meta"]["group_id"] == "group_555"
            assert replay["meta"]["sender_id"] == "replay-sender"
            assert runtime.calls == 1
            assert runtime.note_calls == 1
            assert bridge.calls == 1
            assert complete_calls == ["respond"]
            assert len(owner_session_ids) == 3
            assert inspect_db.query(ChatLog).filter_by(role="ambient").count() == 1
            claim = inspect_db.query(InboundMessageClaim).one()
            assert claim.session_id == "group_555"
            assert claim.status == "completed"
            assert claim.attempt_count == 1
        finally:
            release_timing.set()
            if first_task is not None and not first_task.done():
                first_task.cancel()
                await asyncio.gather(first_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_persisted_reply_recovers_after_claim_complete_failure_without_second_bridge(
    tmp_path,
    monkeypatch,
):
    from contextlib import ExitStack

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.group_ingress import response_contract
    from app.group_ingress.service import GroupIngressService
    from core import database
    from core.database import Base, ChatLog, InboundMessageClaim
    from core.inbound_claim_lifecycle import InboundClaimOwner

    runtime_calls = 0
    bridge_calls = 0
    format_calls: list[str] = []
    expand_calls: list[str] = []

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            nonlocal runtime_calls
            runtime_calls += 1
            return {
                "action": "continue",
                "generation": 1,
                "reason": "respond",
                "pending_text": "hello",
                "source_message_ids": ["recoverable-message"],
            }

        def note_bot_replied(self, *_args, **_kwargs):
            return None

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            nonlocal bridge_calls
            bridge_calls += 1
            return "只生成一次的原始回复"

        def pop_last_reply_meta(self, _session_id):
            return {"quote": "引用"}

    def format_reply(answer, *, max_chars):
        assert max_chars == 4000
        format_calls.append(answer)
        return f"formatted:{answer}"

    def expand_reply(answer, *, allow_base64):
        assert allow_base64 is False
        expand_calls.append(answer)
        return f"expanded:{answer}"

    engine = create_engine(
        f"sqlite:///{tmp_path / 'recoverable-complete.db'}",
        connect_args={"check_same_thread": False, "timeout": 1.0},
    )
    with ExitStack() as stack:
        stack.callback(engine.dispose)
        Base.metadata.create_all(engine)
        stack.callback(Base.metadata.drop_all, bind=engine)
        Session = sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
        )
        db_first = stack.enter_context(Session())
        db_second = stack.enter_context(Session())
        inspect_db = stack.enter_context(Session())
        dbapi_identities = {
            id(session.connection().connection.dbapi_connection)
            for session in (db_first, db_second, inspect_db)
        }
        assert len(dbapi_identities) == 3
        for session in (db_first, db_second, inspect_db):
            session.rollback()

        monkeypatch.setattr(database, "SessionLocal", Session)
        monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
        monkeypatch.setattr(
            response_contract.h,
            "format_group_reply_for_transport",
            format_reply,
        )
        monkeypatch.setattr(
            "core.generated_images.expand_generated_image_refs_in_content",
            expand_reply,
        )
        bridge = Bridge()
        original_complete = InboundClaimOwner.complete
        complete_calls = 0
        complete_error = RuntimeError("claim complete unavailable")

        async def fail_first_complete(owner, completion):
            nonlocal complete_calls
            complete_calls += 1
            if complete_calls == 1:
                raise complete_error
            return await original_complete(owner, completion)

        monkeypatch.setattr(InboundClaimOwner, "complete", fail_first_complete)
        with pytest.raises(RuntimeError) as first_error:
            await GroupIngressService(
                db=db_first,
                bridge_provider=lambda: bridge,
            ).handle(
                _request(
                    group_id="recoverable",
                    message_id="recoverable-message",
                    sender_id="first-sender",
                )
            )
        assert first_error.value is complete_error
        assistant = inspect_db.query(ChatLog).filter_by(role="assistant").one()
        assert assistant.content == "只生成一次的原始回复"
        inspect_db.rollback()

        second = await GroupIngressService(
            db=db_second,
            bridge_provider=lambda: bridge,
        ).handle(
            _request(
                group_id="group_recoverable",
                message_id="recoverable-message",
                sender_id="first-sender",
            )
        )

        assert second["reply"] == "expanded:formatted:只生成一次的原始回复"
        assert second["meta"]["group_id"] == "group_recoverable"
        assert second["meta"]["sender_id"] == "first-sender"
        assert runtime_calls == 1
        assert bridge_calls == 1
        assert complete_calls == 2
        assert format_calls == ["只生成一次的原始回复"] * 2
        assert expand_calls == ["formatted:只生成一次的原始回复"] * 2
        assert inspect_db.query(ChatLog).filter_by(role="assistant").count() == 1
        claim = inspect_db.query(InboundMessageClaim).one()
        assert claim.status == "completed"
        assert claim.attempt_count == 2


@pytest.mark.parametrize(
    "corruption",
    ["missing-marker", "unknown-version", "wrong-request", "multiple-candidates"],
)
@pytest.mark.asyncio
async def test_corrupt_recovery_marker_fails_closed_before_runtime_or_bridge(
    db_session,
    monkeypatch,
    corruption,
):
    import json

    from app.group_ingress import helpers as group_helpers
    from app.group_ingress import response_contract
    from app.group_ingress.recovery import (
        attach_group_completion_recovery,
        attach_group_request_fingerprint,
        build_group_business_input,
        group_business_input_sha256,
    )
    from app.group_ingress.service import GroupIngressService
    from core.database import ChatLog, InboundMessageClaim
    from core.inbound_idempotency import (
        acquire_inbound_claim,
        fail_inbound_claim,
        normalize_inbound_claim_key,
    )

    runtime_calls = 0
    bridge_calls = 0
    message_id = f"corrupt-recovery-{corruption}"
    group_id = f"corrupt-recovery-{corruption}"
    req = _request(
        group_id=group_id,
        message_id=message_id,
        sender_id="sender-a",
        message="原正文",
    )
    key = normalize_inbound_claim_key(
        "qq",
        "group",
        f"group_{group_id}",
        message_id,
    )
    assert key is not None
    message_text = group_helpers.build_group_message_text(req)
    message_meta = group_helpers.build_group_message_meta(req, [])
    request_sha256 = group_business_input_sha256(
        build_group_business_input(
            req,
            key=key,
            message_text=message_text,
            message_meta=message_meta,
            sticker_payloads=group_helpers.group_sticker_payloads(req),
        )
    )
    ambient_meta = attach_group_request_fingerprint(
        message_meta,
        request_sha256,
    )

    decision = acquire_inbound_claim(db_session, key)
    assert decision.handle is not None
    assert fail_inbound_claim(db_session, decision.handle, "seed failure") is True
    db_session.add(
        ChatLog(
            user_id=key.session_id,
            session_id=key.session_id,
            sender_name="小明",
            role="ambient",
            content="[小明]: 原正文",
            processed=1,
            message_id=key.message_id,
            meta_json=json.dumps(ambient_meta, ensure_ascii=False),
        )
    )

    completion = response_contract.build_completed_group_response(
        outcome="respond",
        reply="corrupt reply",
    )
    valid_meta = attach_group_completion_recovery(
        {"kind": "group_reply"},
        key=key,
        request_sha256=request_sha256,
        completion=completion,
    )
    if corruption == "missing-marker":
        candidate_metas = [{"kind": "group_reply"}]
    elif corruption == "unknown-version":
        invalid_meta = json.loads(json.dumps(valid_meta))
        invalid_meta["inbound_claim_recovery"]["schema_version"] = 99
        candidate_metas = [invalid_meta]
    elif corruption == "wrong-request":
        candidate_metas = [
            attach_group_completion_recovery(
                {"kind": "group_reply"},
                key=key,
                request_sha256="b" * 64,
                completion=completion,
            )
        ]
    else:
        candidate_metas = [valid_meta, valid_meta]

    for candidate_meta in candidate_metas:
        db_session.add(
            ChatLog(
                user_id=key.session_id,
                session_id=key.session_id,
                role="assistant",
                content="corrupt reply",
                processed=1,
                message_id=key.message_id,
                meta_json=json.dumps(candidate_meta, ensure_ascii=False),
            )
        )
    db_session.commit()

    class Runtime:
        async def process_message(self, *_args, **_kwargs):
            nonlocal runtime_calls
            runtime_calls += 1
            return {"action": "no_reply"}

    class Bridge:
        async def handle_message(self, *_args, **_kwargs):
            nonlocal bridge_calls
            bridge_calls += 1
            return "不应调用"

    monkeypatch.setattr("core.timing_runtime.get_group_runtime", lambda: Runtime())
    payload = await GroupIngressService(
        db=db_session,
        bridge_provider=lambda: Bridge(),
    ).handle(
        _request(
            group_id=f"group_{group_id}",
            message_id=message_id,
            sender_id="sender-a",
            message="原正文",
        )
    )

    assert payload["action"] == "no_reply"
    assert "recovery" in payload["reason"]
    assert runtime_calls == 0
    assert bridge_calls == 0
    claim = db_session.query(InboundMessageClaim).one()
    assert claim.status == "failed"
    assert claim.attempt_count == 2
