from core.database import SystemSetting
from core.sandbox.tool_service import resolve_sandbox_setting
from core.settings_service import settings


def test_session_and_network_hard_switches_default_to_disabled(db_session):
    assert resolve_sandbox_setting(
        db_session,
        "sandbox.session_execution_allowed",
    ) is False
    assert resolve_sandbox_setting(
        db_session,
        "sandbox.developer_network_allowed",
    ) is False


def test_database_cannot_override_session_and_network_hard_switches(
    db_session,
    monkeypatch,
):
    db_session.add_all([
        SystemSetting(
            key="sandbox.session_execution_allowed",
            value="true",
        ),
        SystemSetting(
            key="sandbox.developer_network_allowed",
            value="true",
        ),
    ])
    db_session.commit()
    monkeypatch.delenv(
        "NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED",
        raising=False,
    )
    monkeypatch.delenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        raising=False,
    )
    settings.invalidate()

    assert resolve_sandbox_setting(
        db_session,
        "sandbox.session_execution_allowed",
    ) is False
    assert resolve_sandbox_setting(
        db_session,
        "sandbox.developer_network_allowed",
    ) is False


def test_environment_can_enable_hard_switches_despite_database_false(
    db_session,
    monkeypatch,
):
    db_session.add_all([
        SystemSetting(
            key="sandbox.session_execution_allowed",
            value="false",
        ),
        SystemSetting(
            key="sandbox.developer_network_allowed",
            value="false",
        ),
    ])
    db_session.commit()
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_SESSION_EXECUTION_ALLOWED",
        "true",
    )
    monkeypatch.setenv(
        "NANOBOT_SANDBOX_DEVELOPER_NETWORK_ALLOWED",
        "true",
    )
    settings.invalidate()

    assert resolve_sandbox_setting(
        db_session,
        "sandbox.session_execution_allowed",
    ) is True
    assert resolve_sandbox_setting(
        db_session,
        "sandbox.developer_network_allowed",
    ) is True
