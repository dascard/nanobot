from __future__ import annotations

import json
from pathlib import Path

import pytest

from api import routes
from api.routes import ChatProxyRequest
from core.database import ChatLog, ConversationTurn, SensitiveData


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _make_req(**updates) -> ChatProxyRequest:
    data = {
        "user_id": "u-persist",
        "session_id": "private_u-persist",
        "query": "原始消息",
        "sender_name": "用户",
        "session_name": "私聊",
        "client_meta": {"platform": "qq", "trace": {"request_id": "req-1"}},
    }
    data.update(updates)
    return ChatProxyRequest(**data)


def test_chat_persistence_module_does_not_import_parent_routes_or_sync_awaitable():
    source = _source("api/chat_persistence.py")

    assert "from api.routes" not in source
    assert "import api.routes" not in source
    assert "asyncio.run" not in source
    assert "run_awaitable_sync" not in source


def test_parent_persistence_wrappers_keep_api_routes_module():
    assert routes._safe_meta.__module__ == "api.routes"
    assert routes._persist_chat_turn.__module__ == "api.routes"


def test_safe_meta_facade_matches_chat_persistence_module():
    from api import chat_persistence

    assert routes._safe_meta('{"a": 1}') == {"a": 1}
    assert routes._safe_meta("[]") == {}
    assert routes._safe_meta("{bad") == {}
    assert routes._safe_meta('{"a": 1}') == chat_persistence.safe_meta('{"a": 1}')


def test_persist_chat_turn_silent_masks_logs_and_saves_sensitive_data(db_session):
    req = _make_req(
        query="敏感原文",
        files=["https://example.com/a.png"],
        user_id="u-silent",
        session_id="private_u-silent",
    )

    pending = routes._persist_chat_turn(
        db_session,
        req,
        "（数据中转，自动静默）",
        guardrail_status="silent",
    )

    assert pending == 2
    user_log = db_session.query(ChatLog).filter_by(user_id="u-silent", role="user").one()
    user_turn = db_session.query(ConversationTurn).filter_by(user_id="u-silent", role="user").one()
    sensitive = db_session.query(SensitiveData).filter_by(user_id="u-silent").one()
    assert user_log.content == "[敏感数据]"
    assert user_turn.content == "[敏感数据]"
    assert "敏感原文" in sensitive.content
    assert "https://example.com/a.png" in sensitive.content


def test_persist_chat_turn_injection_uses_safe_prompt_and_processed_minus_one(db_session):
    req = _make_req(user_id="u-injection", session_id="private_u-injection")

    pending = routes._persist_chat_turn(
        db_session,
        req,
        "拒绝注入",
        guardrail_status="injection",
    )

    assert pending == 0
    user_log = db_session.query(ChatLog).filter_by(user_id="u-injection", role="user").one()
    assistant_log = db_session.query(ChatLog).filter_by(
        user_id="u-injection",
        role="assistant",
    ).one()
    user_turn = db_session.query(ConversationTurn).filter_by(
        user_id="u-injection",
        role="user",
    ).one()
    assert user_log.processed == -1
    assert assistant_log.processed == -1
    assert user_log.content == "[安全提示: 检测到注入已被拦截]"
    assert user_turn.content == "[安全提示: 检测到注入已被拦截]"
    assert db_session.query(SensitiveData).filter_by(user_id="u-injection").count() == 0


def test_persist_chat_turn_html_answer_full_archive_summary_context(db_session):
    html = "<!doctype html><html><body>完整报告</body></html>"
    req = _make_req(user_id="u-html", session_id="private_u-html")

    routes._persist_chat_turn(db_session, req, html)

    assistant_log = db_session.query(ChatLog).filter_by(user_id="u-html", role="assistant").one()
    assistant_turn = db_session.query(ConversationTurn).filter_by(
        user_id="u-html",
        role="assistant",
    ).one()
    turn_meta = json.loads(assistant_turn.meta_json or "{}")
    assert assistant_log.content == html
    assert assistant_turn.content == f"[HTML报告: 已渲染为图片/HTML，{len(html)}字符]"
    assert turn_meta["kind"] == "artifact_summary"


def test_persist_chat_turn_keeps_artifact_refs_but_removes_binary_and_host_paths(
    db_session,
):
    artifact_id = "art_" + "a" * 48
    req = _make_req(
        user_id="u-artifact-history",
        session_id="private_u-artifact-history",
        query=(
            "请处理 data:image/png;base64,QUJDREVGR0g= "
            "以及 /srv/nanobot/private/input.png"
        ),
        files=[
            "file:///home/service/private.png",
            f"artifact://{artifact_id}",
            "https://cdn.example/input.png?signature=secret",
        ],
    )
    answer = (
        f"结果 [artifact:{artifact_id}] "
        "base64://QUJDREVGR0g= /mnt/d/private/output.png "
        f"[asset_download:{'a' * 32}.{'b' * 32}]"
    )

    routes._persist_chat_turn(db_session, req, answer)

    contents = [
        row.content
        for row in db_session.query(ChatLog)
        .filter_by(user_id="u-artifact-history")
        .all()
    ] + [
        row.content
        for row in db_session.query(ConversationTurn)
        .filter_by(user_id="u-artifact-history")
        .all()
    ]
    serialized = "\n".join(contents)
    assert f"[artifact:{artifact_id}]" in serialized
    assert f"artifact://{artifact_id}" in serialized
    assert "https://cdn.example/input.png" in serialized
    for forbidden in (
        "base64://",
        "data:image",
        "/srv/nanobot",
        "/mnt/d/private",
        "file:///home",
        "signature=secret",
        "[asset_download:",
    ):
        assert forbidden not in serialized


def test_persist_chat_turn_source_ids_prepend_message_id_without_duplicate(db_session):
    req = _make_req(
        user_id="u-source",
        session_id="private_u-source",
        message_id="m1",
        source_message_ids=["m2", "m1"],
    )

    routes._persist_chat_turn(db_session, req, "已处理")

    user_log = db_session.query(ChatLog).filter_by(user_id="u-source", role="user").one()
    user_turn = db_session.query(ConversationTurn).filter_by(user_id="u-source", role="user").one()
    assert json.loads(user_log.source_message_ids_json) == ["m2", "m1"]
    assert json.loads(user_turn.source_message_ids_json) == ["m2", "m1"]


def test_persist_chat_turn_prompt_audit_meta_marks_assistant_processed(db_session):
    req = _make_req(user_id="u-audit", session_id="private_u-audit")

    routes._persist_chat_turn(
        db_session,
        req,
        "（无回复内容）",
        assistant_meta={
            "kind": "empty_reply",
            "no_context": True,
            "no_send": True,
            "agent_result": "prompt_v2_audit_failed",
        },
        assistant_processed=1,
    )

    assistant_log = db_session.query(ChatLog).filter_by(user_id="u-audit", role="assistant").one()
    assistant_turn = db_session.query(ConversationTurn).filter_by(
        user_id="u-audit",
        role="assistant",
    ).one()
    assistant_meta = json.loads(assistant_turn.meta_json or "{}")
    assert assistant_log.processed == 1
    assert assistant_meta["kind"] == "empty_reply"
    assert assistant_meta["no_context"] is True
    assert assistant_meta["no_send"] is True
    assert assistant_meta["agent_result"] == "prompt_v2_audit_failed"


def test_persist_chat_turn_timing_gate_written_to_all_expected_meta(db_session):
    req = _make_req(user_id="u-timing", session_id="private_u-timing")
    timing_meta = {
        "mode": "private",
        "action": "reply_now",
        "scoring": {"stage": "unit", "action": "continue"},
    }

    routes._persist_chat_turn(db_session, req, "计时回复", timing_meta=timing_meta)

    logs = db_session.query(ChatLog).filter_by(user_id="u-timing").all()
    turns = db_session.query(ConversationTurn).filter_by(user_id="u-timing").all()
    for row in [*logs, *turns]:
        meta = json.loads(row.meta_json or "{}")
        assert meta["timing_gate"] == timing_meta


def test_persist_chat_turn_pending_count_returns_zero_when_evolution_running(db_session):
    from core.evolution import _evolution_running

    req = _make_req(user_id="u-running", session_id="private_u-running")
    _evolution_running.add("u-running")
    try:
        pending = routes._persist_chat_turn(db_session, req, "正在进化时的回复")
    finally:
        _evolution_running.discard("u-running")

    assert pending == 0
    assert db_session.query(ChatLog).filter_by(user_id="u-running").count() == 2
    assert db_session.query(ConversationTurn).filter_by(user_id="u-running").count() == 2


def _claimed_persistence_input(message_id: str = "claimed-message"):
    from api.chat_persistence import ChatTurnPersistenceInput

    return ChatTurnPersistenceInput(
        user_id="u-claimed",
        session_id="private_u-claimed",
        query="需要可恢复的请求",
        files=["img://claimed"],
        sender_name="用户",
        session_name="私聊",
        message_id=message_id,
        source_message_ids=["source-claimed"],
        client_meta={"platform": "qq", "chat_type": "private"},
    )


def _claimed_key(message_id: str = "claimed-message"):
    from core.inbound_idempotency import InboundClaimKey

    return InboundClaimKey(
        "qq",
        "private",
        "private_u-claimed",
        message_id,
    )


def _claimed_completion(reply: str = "可恢复回复"):
    from api.chat_response_contract import build_completed_inbound_response

    return build_completed_inbound_response(
        outcome="respond",
        reply=reply,
        reply_meta={"send_mode": "quote"},
        reason="answered",
        source="bridge",
        guardrail_status="safe",
    )


def test_private_request_journal_is_unique_reusable_and_bypass_safe(db_session):
    from api.chat_persistence import ensure_private_request_journal

    first = ensure_private_request_journal(
        db_session,
        _claimed_persistence_input(),
        key=_claimed_key(),
        request_sha256="a" * 64,
    )
    second = ensure_private_request_journal(
        db_session,
        _claimed_persistence_input(),
        key=_claimed_key(),
        request_sha256="a" * 64,
    )
    bypass = ensure_private_request_journal(
        db_session,
        _claimed_persistence_input(message_id=""),
        key=None,
        request_sha256="",
    )

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert bypass is None
    rows = db_session.query(ChatLog).filter_by(
        session_id="private_u-claimed",
        role="user",
    ).all()
    assert len(rows) == 1
    assert rows[0].processed == 1
    assert rows[0].message_id == "claimed-message"
    assert json.loads(rows[0].meta_json)["kind"] == "private_inbound_request"


def test_private_request_journal_rejects_changed_business_fingerprint(db_session):
    from api.chat_persistence import ensure_private_request_journal
    from api.chat_recovery import PrivateRequestMismatchError

    ensure_private_request_journal(
        db_session,
        _claimed_persistence_input(),
        key=_claimed_key(),
        request_sha256="a" * 64,
    )

    with pytest.raises(PrivateRequestMismatchError):
        ensure_private_request_journal(
            db_session,
            _claimed_persistence_input(),
            key=_claimed_key(),
            request_sha256="b" * 64,
        )


def test_persist_claimed_chat_turn_commits_recovery_and_business_rows_atomically(
    db_session,
):
    from api.chat_persistence import (
        ensure_private_request_journal,
        persist_claimed_chat_turn,
    )
    from api.chat_recovery import load_private_recoverable_completion

    ensure_private_request_journal(
        db_session,
        _claimed_persistence_input(),
        key=_claimed_key(),
        request_sha256="a" * 64,
    )

    result = persist_claimed_chat_turn(
        db_session,
        _claimed_persistence_input(),
        "可恢复回复",
        key=_claimed_key(),
        request_sha256="a" * 64,
        completion=_claimed_completion(),
    )

    assert result.pending == 2
    assert result.completion.unprocessed_logs == 2
    assert db_session.query(ChatLog).filter_by(user_id="u-claimed").count() == 2
    assert db_session.query(ConversationTurn).filter_by(user_id="u-claimed").count() == 2
    journal = db_session.query(ChatLog).filter_by(
        user_id="u-claimed",
        role="user",
    ).one()
    assistant = db_session.query(ChatLog).filter_by(
        user_id="u-claimed",
        role="assistant",
    ).one()
    assert journal.content.startswith("需要可恢复的请求")
    assert assistant.message_id == "claimed-message"
    assert load_private_recoverable_completion(
        db_session,
        key=_claimed_key(),
        request_sha256="a" * 64,
    ) == result.completion


def test_persist_claimed_chat_turn_rolls_back_every_success_write_on_failure(
    db_session,
    monkeypatch,
):
    from api import chat_persistence

    chat_persistence.ensure_private_request_journal(
        db_session,
        _claimed_persistence_input(),
        key=_claimed_key(),
        request_sha256="a" * 64,
    )

    def fail_marker(*_args, **_kwargs):
        raise RuntimeError("marker write failed")

    monkeypatch.setattr(
        chat_persistence.chat_recovery,
        "attach_private_completion_recovery",
        fail_marker,
    )

    with pytest.raises(RuntimeError, match="marker write failed"):
        chat_persistence.persist_claimed_chat_turn(
            db_session,
            _claimed_persistence_input(),
            "可恢复回复",
            key=_claimed_key(),
            request_sha256="a" * 64,
            completion=_claimed_completion(),
        )

    db_session.expire_all()
    rows = db_session.query(ChatLog).filter_by(user_id="u-claimed").all()
    assert len(rows) == 1
    assert rows[0].role == "user"
    assert "inbound_claim_recovery" not in json.loads(rows[0].meta_json)
    assert db_session.query(ConversationTurn).filter_by(user_id="u-claimed").count() == 0


def test_persist_private_claim_completion_marks_journal_without_fake_turns(db_session):
    from api.chat_persistence import (
        ensure_private_request_journal,
        persist_private_claim_completion,
    )
    from api.chat_recovery import load_private_recoverable_completion
    from api.chat_response_contract import build_completed_inbound_response

    ensure_private_request_journal(
        db_session,
        _claimed_persistence_input(),
        key=_claimed_key(),
        request_sha256="a" * 64,
    )
    completion = build_completed_inbound_response(
        outcome="no_reply",
        reason="timing_gate_no_reply",
    )

    stored = persist_private_claim_completion(
        db_session,
        _claimed_persistence_input(),
        key=_claimed_key(),
        request_sha256="a" * 64,
        completion=completion,
        journal_content="命中静默规则",
        journal_processed=1,
    )

    assert stored == completion
    assert db_session.query(ChatLog).filter_by(user_id="u-claimed").count() == 1
    assert db_session.query(ConversationTurn).filter_by(user_id="u-claimed").count() == 0
    journal = db_session.query(ChatLog).filter_by(user_id="u-claimed").one()
    assert journal.content == "命中静默规则"
    assert journal.processed == 1
    assert load_private_recoverable_completion(
        db_session,
        key=_claimed_key(),
        request_sha256="a" * 64,
    ) == completion
