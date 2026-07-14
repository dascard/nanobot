import logging

import pytest


def test_init_prompt_runtimes_initializes_canonical_prompt_runtime(monkeypatch, caplog):
    from bootstrap import prompt_runtime

    called = {}

    monkeypatch.setattr("core.prompt_v2.template_registry.init_prompt_v2_runtime_dir", lambda: called.setdefault("v2", {
        "copied": ["chat/main.md"],
        "runtime_dir": "/tmp/v2",
        "source_dir": "/tmp/v2-default",
        "task_contracts": [{"task_key": "tasks/memory_extract", "source": "default", "invalid_sources": ["runtime"]}],
    }))
    monkeypatch.setattr("core.settings_service.settings.get", lambda _key, _default=None: "v2")

    logger = logging.getLogger("test.prompt_runtime.v2")
    with caplog.at_level(logging.INFO, logger=logger.name):
        prompt_runtime.init_prompt_runtimes(logger)

    assert called["v2"]["copied"] == ["chat/main.md"]
    assert "[PromptRuntime] initialized 1 templates" in caplog.text
    assert "tasks/memory_extract" in caplog.text
    assert "fallback source=default" in caplog.text
    assert "[PromptV2]" not in caplog.text


def test_init_prompt_runtimes_warns_when_effective_engine_is_v1(monkeypatch, caplog):
    from bootstrap import prompt_runtime

    monkeypatch.setattr("core.prompt_v2.template_registry.init_prompt_v2_runtime_dir", lambda: {
        "copied": [],
        "runtime_dir": "/tmp/v2",
        "source_dir": "/tmp/v2-default",
    })
    monkeypatch.setattr("core.settings_service.settings.get", lambda _key, _default=None: "v1")

    logger = logging.getLogger("test.prompt_runtime.rollback")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        prompt_runtime.init_prompt_runtimes(logger)

    assert "Prompt Runtime 当前有效 engine=v1" in caplog.text
    assert "旧版运行时已下线" in caplog.text


def test_init_prompt_runtimes_fails_closed_for_invalid_active_flow(monkeypatch):
    from types import SimpleNamespace

    from bootstrap import prompt_runtime
    from core.prompt_v2.flow import PromptFlowError

    monkeypatch.setattr(
        "core.prompt_v2.template_registry.init_prompt_v2_runtime_dir",
        lambda: {
            "copied": [],
            "runtime_dir": "/tmp/v2",
            "source_dir": "/tmp/v2-default",
        },
    )
    monkeypatch.setattr(
        "core.prompt_v2.flow.load_flow",
        lambda: SimpleNamespace(flow={"version": 1}, path="bad-flow.json", source="runtime"),
    )

    def fail_validation(_flow):
        raise PromptFlowError("active flow contract invalid")

    monkeypatch.setattr("core.prompt_v2.flow.validate_runtime_contract", fail_validation, raising=False)

    with pytest.raises(PromptFlowError, match="active flow contract invalid"):
        prompt_runtime.init_prompt_runtimes(logging.getLogger("test.prompt_runtime.invalid"))


def test_init_prompt_runtimes_logs_flow_migration(monkeypatch, caplog):
    from bootstrap import prompt_runtime

    monkeypatch.setattr(
        "core.prompt_v2.template_registry.init_prompt_v2_runtime_dir",
        lambda: {
            "copied": [],
            "runtime_dir": "/tmp/v2",
            "source_dir": "/tmp/v2-default",
            "flow_migrated": True,
            "flow_backup_path": "/tmp/backups/legacy-flow.json.bak",
        },
    )
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda _key, _default=None: "prompt",
    )

    logger = logging.getLogger("test.prompt_runtime.flow-migration")
    with caplog.at_level(logging.INFO, logger=logger.name):
        prompt_runtime.init_prompt_runtimes(logger)

    assert "runtime flow migrated" in caplog.text
    assert "legacy-flow.json.bak" in caplog.text


def test_init_prompt_runtimes_re_raises_runtime_migration_failure(
    monkeypatch,
    caplog,
):
    from bootstrap import prompt_runtime
    from core.prompt_v2.flow_migrations import PromptFlowMigrationError

    downstream_calls = []

    def fail_init():
        raise PromptFlowMigrationError("unsafe legacy flow")

    monkeypatch.setattr(
        "core.prompt_v2.template_registry.init_prompt_v2_runtime_dir",
        fail_init,
    )
    monkeypatch.setattr(
        "core.prompt_v2.flow.load_flow",
        lambda: downstream_calls.append("load_flow"),
    )
    monkeypatch.setattr(
        "core.settings_service.settings.get",
        lambda *_args: downstream_calls.append("settings"),
    )
    logger = logging.getLogger("test.prompt_runtime.migration-failure")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        with pytest.raises(PromptFlowMigrationError, match="unsafe legacy flow"):
            prompt_runtime.init_prompt_runtimes(logger)

    assert "init_runtime_dir failed" in caplog.text
    assert downstream_calls == []
    assert any(record.exc_info for record in caplog.records)
