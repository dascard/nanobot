"""Agent Link 独立凭据兼容迁移的安全诊断测试。"""

from __future__ import annotations

import logging


def test_agent_link_token_diagnostic_never_returns_secret():
    from config import agent_link_token_diagnostic

    explicit_secret = "agent-link-secret-must-not-leak"
    api_secret = "api-secret-must-not-leak"
    explicit = agent_link_token_diagnostic({
        "NANOBOT_AGENT_LINK_TOKEN": explicit_secret,
        "NANOBOT_API_TOKEN": api_secret,
    })
    fallback = agent_link_token_diagnostic({
        "NANOBOT_AGENT_LINK_TOKEN": "",
        "NANOBOT_API_TOKEN": api_secret,
    })
    missing = agent_link_token_diagnostic({})

    assert explicit == {
        "configured": True,
        "source": "agent_link_token",
        "fallback": False,
    }
    assert fallback == {
        "configured": True,
        "source": "api_token_fallback",
        "fallback": True,
    }
    assert missing == {
        "configured": False,
        "source": "unconfigured",
        "fallback": False,
    }
    serialized = repr((explicit, fallback, missing))
    assert explicit_secret not in serialized
    assert api_secret not in serialized


def test_agent_link_api_token_fallback_warns_only_once(
    monkeypatch,
    caplog,
):
    import config

    monkeypatch.setenv("NANOBOT_AGENT_LINK_TOKEN", "")
    monkeypatch.setenv(
        "NANOBOT_API_TOKEN",
        "api-secret-must-not-appear-in-log",
    )
    monkeypatch.setattr(
        config,
        "_AGENT_LINK_FALLBACK_WARNING_EMITTED",
        False,
    )
    logger = logging.getLogger("nanobot.agent-link-token.test")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        config.log_agent_link_token_configuration(logger)
        config.log_agent_link_token_configuration(logger)

    assert caplog.text.count("source=api_token_fallback") == 1
    assert "api-secret-must-not-appear-in-log" not in caplog.text
