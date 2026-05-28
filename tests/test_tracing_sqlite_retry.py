from sqlalchemy.exc import OperationalError


def test_reply_contract_tracer_retries_sqlite_locked_commit(db_session, monkeypatch):
    from core import database
    from core.database import ReplyContractCheckLog
    from core.tracing import ReplyContractTracer

    original_commit = db_session.commit
    commit_calls = {"count": 0}

    def flaky_commit():
        commit_calls["count"] += 1
        if commit_calls["count"] == 1:
            raise OperationalError(
                "INSERT INTO reply_contract_check_logs ...",
                {},
                Exception("database is locked"),
            )
        return original_commit()

    monkeypatch.setattr(database, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "commit", flaky_commit)

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
