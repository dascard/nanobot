from __future__ import annotations

import json
from pathlib import Path

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
