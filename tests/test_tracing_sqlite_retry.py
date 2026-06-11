from sqlalchemy.exc import OperationalError


def _fail_first_commit(db_session, monkeypatch):
    original_commit = db_session.commit
    commit_calls = {"count": 0}

    def flaky_commit():
        commit_calls["count"] += 1
        if commit_calls["count"] == 1:
            raise OperationalError(
                "INSERT ...",
                {},
                Exception("database is locked"),
            )
        return original_commit()

    monkeypatch.setattr(db_session, "commit", flaky_commit)
    return commit_calls


def test_reply_contract_tracer_retries_sqlite_locked_commit(db_session, monkeypatch):
    from core import database
    from core.database import ReplyContractCheckLog
    from core.tracing import ReplyContractTracer

    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    commit_calls = _fail_first_commit(db_session, monkeypatch)

    ReplyContractTracer.record_check(
        trace_id="trace-lock",
        run_id="run-lock",
        session_id="s-lock",
        attempt=1,
        raw_output="锁重试输出",
        result="retry_plain_text_repair",
    )

    rows = db_session.query(ReplyContractCheckLog).filter_by(run_id="run-lock").all()
    assert len(rows) == 1
    assert rows[0].raw_output_preview == "锁重试输出"
    assert commit_calls["count"] >= 2


def test_llm_request_tracer_retries_sqlite_locked_commit(db_session, monkeypatch):
    from core import database
    from core.database import LLMApiRequestLog
    from core.tracing import LLMRequestTracer

    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    commit_calls = _fail_first_commit(db_session, monkeypatch)

    log_id = LLMRequestTracer.record_request(
        trace_id="trace-llm-lock",
        run_id="run-llm-lock",
        source="chat",
        provider="test",
        model="test-model",
        request={"messages": [{"role": "user", "content": "锁测试"}]},
    )

    assert log_id > 0
    rows = db_session.query(LLMApiRequestLog).filter_by(run_id="run-llm-lock").all()
    assert len(rows) == 1
    assert rows[0].request_preview
    assert commit_calls["count"] >= 2


def test_tool_tracer_retries_sqlite_locked_commit(db_session, monkeypatch):
    from core import database
    from core.database import ToolCall
    from core.tracing import ToolTracer

    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    commit_calls = _fail_first_commit(db_session, monkeypatch)

    tool_call_id = ToolTracer.start_tool_call(
        trace_id="trace-tool-lock",
        run_id="run-tool-lock",
        tool_name="image_summary",
        args={"file": "x"},
    )

    assert tool_call_id
    rows = db_session.query(ToolCall).filter_by(run_id="run-tool-lock").all()
    assert len(rows) == 1
    assert rows[0].tool_name == "image_summary"
    assert commit_calls["count"] >= 2


def test_chat_turn_persist_retries_sqlite_locked_commit(db_session, monkeypatch):
    from api.routes import ChatProxyRequest, _persist_chat_turn
    from core.database import ChatLog, ConversationTurn

    commit_calls = _fail_first_commit(db_session, monkeypatch)

    _persist_chat_turn(
        db_session,
        ChatProxyRequest(
            user_id="u-lock",
            session_id="private_u-lock",
            query="锁测试",
        ),
        "已处理",
    )

    assert db_session.query(ChatLog).filter_by(session_id="private_u-lock").count() == 2
    assert db_session.query(ConversationTurn).filter_by(session_id="private_u-lock").count() == 2
    assert commit_calls["count"] >= 2


def test_submit_log_retries_sqlite_locked_commit(db_session, monkeypatch):
    from api.routes import LogRequest, submit_log
    from core.database import ChatLog, User
    from fastapi import BackgroundTasks

    commit_calls = _fail_first_commit(db_session, monkeypatch)

    response = submit_log(
        LogRequest(user_id="u-submit-lock", role="user", content="锁日志"),
        BackgroundTasks(),
        db_session,
        _auth=True,
    )

    assert response["status"] == "ok"
    assert db_session.query(User).filter_by(id="u-submit-lock").count() == 1
    assert db_session.query(ChatLog).filter_by(user_id="u-submit-lock").count() == 1
    assert commit_calls["count"] >= 2


def test_sqlite_locked_retry_logs_transient_retry_below_warning():
    from core.sqlite_retry import run_sqlite_locked_retry

    calls = {"count": 0}
    logs = {"info": 0, "warning": 0}

    class Logger:
        def info(self, *args, **kwargs):
            logs["info"] += 1

        def warning(self, *args, **kwargs):
            logs["warning"] += 1

    def operation():
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError(
                "INSERT ...",
                {},
                Exception("database is locked"),
            )
        return "ok"

    assert run_sqlite_locked_retry(
        operation,
        logger=Logger(),
        attempts=4,
        base_delay_seconds=0,
    ) == "ok"
    assert calls["count"] == 2
    assert logs["info"] == 1
    assert logs["warning"] == 0


def test_sqlite_locked_retry_warns_when_final_retry_is_next():
    from core.sqlite_retry import run_sqlite_locked_retry

    logs = {"info": 0, "warning": 0}

    class Logger:
        def info(self, *args, **kwargs):
            logs["info"] += 1

        def warning(self, *args, **kwargs):
            logs["warning"] += 1

    def operation():
        raise OperationalError(
            "INSERT ...",
            {},
            Exception("database is locked"),
        )

    try:
        run_sqlite_locked_retry(
            operation,
            logger=Logger(),
            attempts=2,
            base_delay_seconds=0,
        )
    except OperationalError:
        pass
    else:
        raise AssertionError("expected sqlite locked error")

    assert logs["info"] == 0
    assert logs["warning"] == 1
