"""reply_eval 调度化——套件运行内核可复用,调度默认关闭。"""
import pytest


class _SessionWrapper:
    def __init__(self, db_session):
        self._db_session = db_session

    def __getattr__(self, name):
        # 护栏:_db_session 自身缺失时直接抛错,避免 __getattr__ 自引用无限递归
        if name == "_db_session":
            raise AttributeError(name)
        return getattr(self._db_session, name)

    def close(self):
        pass


def _insert_reply_case(db_session, case_id="reply_case_1", expected_action="reply"):
    from core.database import ReplyEvalCase

    row = ReplyEvalCase(
        case_id=case_id,
        title="被叫到",
        chat_type="group",
        input_text="凛音在吗",
        context_json="{}",
        expected_action=expected_action,
        expected_keywords_json="[]",
        forbidden_keywords_json="[]",
        source="manual",
        tags_json="[]",
        enabled=1,
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.mark.asyncio
async def test_run_reply_eval_suite_records_run_and_results(db_session, monkeypatch):
    from api.admin import reply_routes

    async def fake_run_once(body, db):
        return {
            "run_id": "agent-run-1",
            "trace_id": "trace-1",
            "prompt_sha256": "sha256-fake",
            "final": {"action": "reply", "content": "在的"},
            "first_attempt": {"result": "ok", "raw_output": "raw"},
            "metrics": {
                "reply_contract_ok": True,
                "retry_used": False,
                "retry_success": False,
            },
        }

    monkeypatch.setattr(reply_routes, "_run_reply_test_once", fake_run_once)
    _insert_reply_case(db_session)

    result = await reply_routes.run_reply_eval_suite(
        db_session, variant="v2_code_retry", name="调度测试"
    )

    assert result["total"] == 1
    assert result["passed"] == 1
    assert result["failed"] == 0

    from core.database import ReplyEvalResult, ReplyEvalRun

    run = db_session.query(ReplyEvalRun).one()
    assert run.name == "调度测试"
    assert run.variant == "v2_code_retry"
    assert run.total == 1
    assert run.passed == 1
    row = db_session.query(ReplyEvalResult).one()
    assert row.case_id == "reply_case_1"
    assert row.actual_action == "reply"
    assert row.passed == 1


def test_run_reply_eval_tick_disabled_returns_none(monkeypatch):
    from bootstrap import schedulers
    from core.settings_service import settings

    monkeypatch.setattr(settings, "get_bool", lambda key, default=False: False)

    assert schedulers.run_reply_eval_tick() is None


def test_run_reply_eval_tick_enabled_runs_suite(db_session, monkeypatch):
    from api.admin import reply_routes
    from bootstrap import schedulers
    from core.settings_service import settings

    monkeypatch.setattr(settings, "get_bool", lambda key, default=False: True)
    monkeypatch.setattr(settings, "get_str", lambda key, default="": "v2_code_retry")

    captured = {}

    async def fake_suite(db, *, variant, name="", case_ids=None, limit=50):
        captured["variant"] = variant
        captured["name"] = name
        return {"total": 2, "passed": 2, "failed": 0}

    monkeypatch.setattr(reply_routes, "run_reply_eval_suite", fake_suite)
    monkeypatch.setattr(
        "core.database.SessionLocal", lambda: _SessionWrapper(db_session)
    )

    result = schedulers.run_reply_eval_tick()

    assert result == {"total": 2, "passed": 2, "failed": 0}
    assert captured["variant"] == "v2_code_retry"
    assert "scheduled" in captured["name"]


def test_reply_eval_schedule_config_defaults():
    from core.config_registry import SETTING_DEFS

    assert SETTING_DEFS["eval.reply_eval_schedule_enabled"].default is False
    assert SETTING_DEFS["eval.reply_eval_interval_hours"].default == 24
    assert SETTING_DEFS["eval.reply_eval_variant"].default == "v2_code_retry"
