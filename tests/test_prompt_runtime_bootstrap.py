import logging


def test_init_prompt_runtimes_initializes_prompt_v2(monkeypatch, caplog):
    from bootstrap import prompt_runtime

    called = {}

    monkeypatch.setattr("core.prompt_v2.template_registry.init_prompt_v2_runtime_dir", lambda: called.setdefault("v2", {
        "copied": ["chat/main.md"],
        "runtime_dir": "/tmp/v2",
        "source_dir": "/tmp/v2-default",
    }))
    monkeypatch.setattr("core.settings_service.settings.get", lambda _key, _default=None: "v2")

    logger = logging.getLogger("test.prompt_runtime.v2")
    with caplog.at_level(logging.INFO, logger=logger.name):
        prompt_runtime.init_prompt_runtimes(logger)

    assert called["v2"]["copied"] == ["chat/main.md"]
    assert "[PromptV2] initialized 1 templates" in caplog.text


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
