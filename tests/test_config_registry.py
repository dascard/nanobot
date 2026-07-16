import logging
from types import SimpleNamespace

import pytest


def _settings_service_with_rows(rows):
    from core.settings_service import SettingsService

    stored_rows = [SimpleNamespace(key=key, value=value) for key, value in rows]

    class FakeQuery:
        def all(self):
            return stored_rows

    class FakeSession:
        def query(self, _model):
            return FakeQuery()

        def close(self):
            return None

    return SettingsService(session_factory=FakeSession), stored_rows


def test_proactive_outreach_settings_are_registered_as_plain_boolean_switch():
    from core.config_registry import SETTING_DEFS

    expected = {
        "proactive_outreach.enabled": (False, "bool"),
        "proactive_outreach.fallback_interval_min": (120, "int"),
        "proactive_outreach.min_interval_min": (30, "int"),
        "proactive_outreach.max_check_interval_min": (1440, "int"),
        "proactive_outreach.max_silence_min": (2880, "int"),
        "proactive_outreach.ambiguous_hold_min": (120, "int"),
        "proactive_outreach.surge_min_prob": (0.1, "float"),
        "proactive_outreach.surge_max_prob": (0.6, "float"),
    }

    for key, (default, value_type) in expected.items():
        setting = SETTING_DEFS[key]
        assert setting.key == key
        assert setting.default == default
        assert setting.value_type == value_type
        assert setting.category == "proactive"

    proactive_keys = {key for key in SETTING_DEFS if key.startswith("proactive_outreach.")}
    assert proactive_keys == set(expected)
    assert "proactive_outreach.mode" not in SETTING_DEFS
    assert all("shadow" not in key and "dry" not in key for key in proactive_keys)
    assert SETTING_DEFS["proactive_outreach.enabled"].restart_required is False

    ambiguous_hold = SETTING_DEFS["proactive_outreach.ambiguous_hold_min"]
    assert ambiguous_hold.env_name == "PROACTIVE_OUTREACH_AMBIGUOUS_HOLD_MIN"
    assert ambiguous_hold.min_value == 1
    assert ambiguous_hold.max_value == 10080


def test_unreviewed_memory_automation_is_disabled_by_default():
    from core.config_registry import SETTING_DEFS

    assert SETTING_DEFS["persona.injection_enabled"].default is False
    assert SETTING_DEFS["persona.auto_update_enabled"].default is False
    assert SETTING_DEFS["group_memory.injection_enabled"].default is False


def test_summary_routes_disable_thinking_by_default():
    from core.config_registry import SETTING_DEFS

    assert SETTING_DEFS["model.route.session_summary.enable_thinking"].default == "false"
    assert SETTING_DEFS["model.route.memory_digest.enable_thinking"].default == "false"


def test_settings_service_reports_database_environment_and_default_sources(monkeypatch):
    monkeypatch.setenv("NEW_API_TIMEOUT", "123")
    monkeypatch.delenv("RAG_RERANKER_SCORE_MODE", raising=False)
    service, _rows = _settings_service_with_rows([("log.level", "DEBUG")])

    database_value = service.get_resolved("log.level")
    environment_value = service.get_resolved("new_api.timeout")
    default_value = service.get_resolved("rag.reranker.score_mode")

    assert (database_value.value, database_value.source) == ("DEBUG", "database")
    assert (environment_value.value, environment_value.source) == (123, "environment")
    assert (default_value.value, default_value.source) == ("sigmoid", "default")


def test_memory_digest_settings_use_distinct_canonical_names():
    from core.config_registry import LEGACY_SETTING_ALIASES, SETTING_DEFS

    enabled = SETTING_DEFS["memory_digest.scheduler_enabled"]
    hour = SETTING_DEFS["memory_digest.schedule_hour"]

    assert (enabled.env_name, enabled.default, enabled.value_type) == (
        "MEMORY_DIGEST_SCHEDULER_ENABLED",
        True,
        "bool",
    )
    assert (hour.env_name, hour.default, hour.value_type) == (
        "MEMORY_DIGEST_SCHEDULE_HOUR",
        4,
        "int",
    )
    assert hour.min_value == 0
    assert hour.max_value == 23
    assert "daily_digest.enabled" not in SETTING_DEFS
    assert "daily_digest.hour" not in SETTING_DEFS
    assert LEGACY_SETTING_ALIASES["memory_digest.scheduler_enabled"].key == (
        "daily_digest.enabled"
    )
    assert LEGACY_SETTING_ALIASES["memory_digest.schedule_hour"].env_name == (
        "DAILY_DIGEST_HOUR"
    )


def test_session_summary_model_uses_dedicated_environment_variable(monkeypatch):
    from core.config_registry import SETTING_DEFS

    model = SETTING_DEFS["model.session_summary"]
    assert model.env_name == "LLM_MODEL_SESSION_SUMMARY"

    monkeypatch.setenv("LLM_MODEL_SESSION_SUMMARY", "summary-model")
    service, _rows = _settings_service_with_rows([])

    resolved = service.get_resolved("model.session_summary")

    assert (resolved.value, resolved.source) == ("summary-model", "environment")


@pytest.mark.parametrize(
    ("rows", "canonical_env", "legacy_env", "expected"),
    [
        (
            [
                ("memory_digest.schedule_hour", "5"),
                ("daily_digest.hour", "6"),
            ],
            "7",
            "8",
            (5, "database"),
        ),
        (
            [("daily_digest.hour", "6")],
            "7",
            "8",
            (7, "environment"),
        ),
        (
            [("daily_digest.hour", "6")],
            None,
            "8",
            (6, "legacy_database"),
        ),
        ([], None, "8", (8, "legacy_environment")),
        ([], None, None, (4, "default")),
    ],
)
def test_memory_digest_setting_precedence(
    monkeypatch,
    rows,
    canonical_env,
    legacy_env,
    expected,
):
    for name in ("MEMORY_DIGEST_SCHEDULE_HOUR", "DAILY_DIGEST_HOUR"):
        monkeypatch.delenv(name, raising=False)
    if canonical_env is not None:
        monkeypatch.setenv("MEMORY_DIGEST_SCHEDULE_HOUR", canonical_env)
    if legacy_env is not None:
        monkeypatch.setenv("DAILY_DIGEST_HOUR", legacy_env)
    service, _stored_rows = _settings_service_with_rows(rows)

    resolved = service.get_resolved("memory_digest.schedule_hour")

    assert (resolved.value, resolved.source) == expected


def test_memory_digest_legacy_bool_environment_is_cast_with_canonical_contract(
    monkeypatch,
):
    monkeypatch.delenv("MEMORY_DIGEST_SCHEDULER_ENABLED", raising=False)
    monkeypatch.setenv("DAILY_DIGEST_ENABLED", "0")
    service, _rows = _settings_service_with_rows([])

    resolved = service.get_resolved("memory_digest.scheduler_enabled")

    assert (resolved.value, resolved.source) == (False, "legacy_environment")


def test_memory_digest_legacy_warning_is_once_only_and_resolution_is_idempotent(
    monkeypatch,
    caplog,
):
    monkeypatch.delenv("MEMORY_DIGEST_SCHEDULE_HOUR", raising=False)
    monkeypatch.delenv("DAILY_DIGEST_HOUR", raising=False)
    service, stored_rows = _settings_service_with_rows([("daily_digest.hour", "6")])

    with caplog.at_level(logging.WARNING, logger="nanobot.settings"):
        first = service.get_resolved("memory_digest.schedule_hour")
        service.invalidate()
        second = service.get_resolved("memory_digest.schedule_hour")

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "daily_digest.hour" in record.getMessage()
    ]
    assert (first.value, first.source) == (6, "legacy_database")
    assert second == first
    assert [(row.key, row.value) for row in stored_rows] == [("daily_digest.hour", "6")]
    assert len(warnings) == 1
    assert "memory_digest.schedule_hour" in warnings[0]
