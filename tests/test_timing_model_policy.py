"""TimingGate 模型层策略解析测试。"""


def test_timing_model_policy_defaults_to_enabled():
    from core.timing_model_policy import resolve_timing_model_policy

    policy = resolve_timing_model_policy(session_id="group_1", platform="qq")

    assert policy.mode == "enabled"
    assert policy.source == "default"


def test_timing_model_policy_session_overrides_platform(monkeypatch):
    from core.timing_model_policy import resolve_timing_model_policy

    values = {
        "timing_gate.model_policy.default": "enabled",
        "timing_gate.model_policy.platforms": '{"web": "shadow"}',
        "timing_gate.model_policy.sessions": '{"group_1": "rules_only"}',
    }

    monkeypatch.setattr(
        "core.timing_model_policy.settings.get_str",
        lambda key, default="": values.get(key, default),
    )

    policy = resolve_timing_model_policy(session_id="group_1", platform="web")

    assert policy.mode == "rules_only"
    assert policy.source == "session:group_1"


def test_timing_model_policy_platform_overrides_default(monkeypatch):
    from core.timing_model_policy import resolve_timing_model_policy

    values = {
        "timing_gate.model_policy.default": "enabled",
        "timing_gate.model_policy.platforms": '{"web": "shadow"}',
        "timing_gate.model_policy.sessions": "{}",
    }

    monkeypatch.setattr(
        "core.timing_model_policy.settings.get_str",
        lambda key, default="": values.get(key, default),
    )

    policy = resolve_timing_model_policy(session_id="group_2", platform="web")

    assert policy.mode == "shadow"
    assert policy.source == "platform:web"


def test_timing_model_policy_normalizes_disabled_alias(monkeypatch):
    from core.timing_model_policy import resolve_timing_model_policy

    values = {
        "timing_gate.model_policy.default": "disabled",
        "timing_gate.model_policy.platforms": "{}",
        "timing_gate.model_policy.sessions": "{}",
    }

    monkeypatch.setattr(
        "core.timing_model_policy.settings.get_str",
        lambda key, default="": values.get(key, default),
    )

    policy = resolve_timing_model_policy(session_id="", platform="")

    assert policy.mode == "rules_only"
    assert policy.source == "default"
