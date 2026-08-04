"""Model Preset 到 KT Provider 的运行时适配测试。"""

from types import SimpleNamespace

import pytest

from core.agent_runtime import RuntimeModelRoute
from nanobot_kt.model_provider_adapter import (
    apply_kt_preset_model_route,
    create_kt_provider,
)
from nanobot_kt.model_runtime import (
    PresetRouteClient,
    ReplyRoutePlan,
    resolve_reply_route_plans,
)


def _transport(driver_type: str, **overrides):
    values = {
        "provider_id": f"{driver_type}-provider",
        "driver_type": driver_type,
        "profile_id": f"{driver_type}-preset",
        "model": f"{driver_type}-model",
        "base_url": "https://gateway.example.com/v1",
        "api_key": "secret",
        "timeout": 90,
        "temperature": 0.25,
        "max_tokens": 8192,
        "max_context": 128000,
        "reasoning_effort": "high",
        "service_tier": "priority",
        "enable_thinking": "true",
        "capabilities": {"supports_tools": True},
        "extra_headers": {"X-Trace": "enabled"},
        "extra_body": {"parallel_tool_calls": True},
        "retry_policy": {"max_retries": 4},
        "driver_options": {},
        "provider_name": driver_type,
        "provider_native_tools": (),
        "codex_account_id": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _ProviderDouble:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.prompt_cache_key = None
        self.emergency_drop_callbacks = []

    def on_emergency_drop(self, callback):
        self.emergency_drop_callbacks.append(callback)


@pytest.mark.parametrize(
    ("driver_type", "module_path", "class_name"),
    [
        ("openai", "kohakuterrarium.llm.openai", "OpenAIProvider"),
        (
            "anthropic",
            "kohakuterrarium.llm.anthropic_provider",
            "AnthropicProvider",
        ),
        (
            "codex",
            "nanobot_kt.codex_accounts",
            "AccountBoundCodexOAuthProvider",
        ),
    ],
)
def test_create_kt_provider_uses_driver_specific_constructor(
    monkeypatch,
    driver_type,
    module_path,
    class_name,
):
    module = __import__(module_path, fromlist=[class_name])
    monkeypatch.setattr(module, class_name, _ProviderDouble)
    transport = _transport(driver_type)
    if driver_type == "anthropic":
        transport = _transport(
            driver_type,
            reasoning_effort="",
            driver_options={
                "auth_as_bearer": True,
                "disable_prompt_caching": True,
            },
        )
    elif driver_type == "codex":
        transport = _transport(
            driver_type,
            base_url="",
            api_key="",
            temperature=None,
            extra_headers={},
            extra_body={},
            codex_account_id="ca_test_account_0001",
        )

    provider = create_kt_provider(transport)

    assert provider.nanobot_profile_id == f"{driver_type}-preset"
    assert provider.nanobot_provider_id == f"{driver_type}-provider"
    assert provider.provider_name == driver_type
    assert provider.kwargs["model"] == f"{driver_type}-model"
    assert provider.kwargs["max_retries"] == 4
    if driver_type == "openai":
        assert provider.kwargs["extra_body"]["reasoning_effort"] == "high"
        assert provider.kwargs["extra_body"]["service_tier"] == "priority"
    elif driver_type == "anthropic":
        assert provider.kwargs["auth_as_bearer"] is True
        assert provider.kwargs["extra_body"]["disable_prompt_caching"] is True
    else:
        assert provider.kwargs["reasoning_effort"] == "high"
        assert provider.kwargs["service_tier"] == "priority"
        assert provider.kwargs["account_id"] == "ca_test_account_0001"


def test_apply_preset_route_replaces_all_kt_provider_references(
    monkeypatch,
):
    current = SimpleNamespace(
        prompt_cache_key="prompt-cache",
    )
    replacement_callbacks = []
    replacement = SimpleNamespace(
        prompt_cache_key=None,
        on_emergency_drop=replacement_callbacks.append,
    )
    registered = []

    class _Registry:
        @staticmethod
        def list_tools():
            return []

        @staticmethod
        def register_tool(tool):
            registered.append(tool)

    agent = SimpleNamespace(
        controller=SimpleNamespace(llm=current),
        llm=current,
        subagent_manager=SimpleNamespace(llm=current),
        registry=_Registry(),
        compact_manager=SimpleNamespace(
            config=SimpleNamespace(enabled=True),
        ),
    )
    monkeypatch.setattr(
        "nanobot_kt.model_provider_adapter.create_kt_provider",
        lambda _transport_value, *, model_id=None: replacement,
    )
    monkeypatch.setattr(
        "kohakuterrarium.builtins.tool_catalog.get_builtin_tool",
        lambda name: {"name": name},
    )
    route = RuntimeModelRoute(
        route_id="reply",
        model_id="gpt-5.4",
        provider_id="codex",
        profile_id="codex-high",
    )
    transport = _transport(
        "codex",
        profile_id="codex-high",
        model="gpt-5.4",
        provider_native_tools=("image_gen",),
    )

    apply_kt_preset_model_route(
        agent,
        route,
        transport,
    )

    assert agent.controller.llm.provider is replacement
    assert agent.llm is agent.controller.llm
    assert agent.subagent_manager.llm is agent.controller.llm
    assert len(replacement_callbacks) == 1
    assert replacement.prompt_cache_key == "prompt-cache"
    assert agent.compact_manager.config.enabled is False
    assert registered == [{"name": "image_gen"}]


def test_preset_route_client_keeps_binding_order_and_capability_filter(
    monkeypatch,
):
    tracker = SimpleNamespace(sync_is_disabled=lambda _model: False)
    monkeypatch.setattr(
        "clients.new_api_client.NewAPIClient.get_failure_tracker",
        lambda: tracker,
    )
    plans = [
        ReplyRoutePlan(
            provider_id="codex",
            registry_provider="codex",
            base_url="",
            api_key="",
            timeout=300,
            profile_id="codex-high",
            model="gpt-5.4",
            cost_input_1m=0,
            cost_output_1m=0,
            capabilities={"supports_tools": True, "supports_image": True},
        ),
        ReplyRoutePlan(
            provider_id="newapi",
            registry_provider="new-api",
            base_url="https://gateway.example.com/v1",
            api_key="secret",
            timeout=120,
            profile_id="openai-balanced",
            model="demo-reasoner",
            cost_input_1m=2.5,
            cost_output_1m=10.0,
            capabilities={"supports_tools": True, "supports_image": False},
        ),
        ReplyRoutePlan(
            provider_id="newapi",
            registry_provider="new-api",
            base_url="https://gateway.example.com/v1",
            api_key="secret",
            timeout=120,
            profile_id="openai-unknown-price",
            model="demo-unknown-price",
            capabilities={"supports_tools": True, "supports_image": False},
        ),
    ]

    candidates = PresetRouteClient(plans).get_ordered_candidates(
        "ignored",
        0,
        required_capabilities={"supports_tools": True},
    )
    image_candidates = PresetRouteClient(plans).get_ordered_candidates(
        "ignored",
        0,
        required_capabilities={"supports_image": True},
    )
    budget_candidates = PresetRouteClient(plans).get_ordered_candidates(
        "ignored",
        0,
        max_cost=1.0,
    )
    paid_candidates = PresetRouteClient(plans).get_ordered_candidates(
        "ignored",
        0,
        avoid_tags=["free", "price_unknown"],
    )

    assert [item["_preset_id"] for item in candidates] == [
        "codex-high",
        "openai-balanced",
        "openai-unknown-price",
    ]
    assert candidates[0]["cost_input_1m"] == 0
    assert candidates[0]["cost_output_1m"] == 0
    assert candidates[0]["tags"] == ["free"]
    assert candidates[1]["cost_input_1m"] == 2.5
    assert candidates[1]["cost_output_1m"] == 10.0
    assert candidates[1]["tags"] == ["paid"]
    assert candidates[2]["tags"] == ["price_unknown"]
    assert [item["_preset_id"] for item in image_candidates] == [
        "codex-high"
    ]
    assert [item["_preset_id"] for item in budget_candidates] == [
        "codex-high",
        "openai-unknown-price",
    ]
    assert [item["_preset_id"] for item in paid_candidates] == [
        "openai-balanced"
    ]


def test_reply_route_skips_driver_whose_runtime_dependency_is_missing(
    monkeypatch,
):
    preset = SimpleNamespace(
        id="anthropic-analysis",
        provider_id="anthropic",
        enabled=True,
    )
    candidate = SimpleNamespace(preset_id=preset.id)
    monkeypatch.setattr(
        "core.model_provider.preset_config.resolve_route_binding_candidates",
        lambda _route_key: [(candidate, SimpleNamespace(preset=preset))],
    )
    monkeypatch.setattr(
        "core.model_provider.provider_config.get_provider_instance",
        lambda _provider_id: SimpleNamespace(
            driver_type="anthropic",
            enabled=True,
            agent_runtime_supported=True,
            runtime_available=False,
            runtime_unavailable_reason="缺少 Python 依赖：anthropic",
            credential_configured=True,
            base_url="https://api.anthropic.com",
        ),
    )

    with pytest.raises(RuntimeError, match="缺少 Python 依赖：anthropic"):
        resolve_reply_route_plans(
            default_base_url="https://fallback.example.com/v1",
            default_api_key="fallback-secret",
        )


def test_reply_route_expands_codex_model_into_session_ordered_accounts(
    monkeypatch,
):
    preset = SimpleNamespace(
        id="codex-default",
        provider_id="codex",
        enabled=True,
        timeout=300,
        model="gpt-5.6-codex",
        temperature=None,
        max_output=32768,
        max_context=200000,
        cost_input_1m=0,
        cost_output_1m=0,
        intelligence=15,
        fallback_only=False,
        input_modalities=("text", "image"),
        output_modalities=("text",),
        reasoning_effort="high",
        service_tier="",
        enable_thinking="auto",
        capabilities={"supports_tools": True, "supports_image": True},
        extra_headers={},
        extra_body={},
        retry_policy={},
        driver_options={},
    )
    candidate = SimpleNamespace(identity="codex/gpt-5.6-codex")
    provider = SimpleNamespace(
        id="codex",
        driver_type="codex",
        enabled=True,
        agent_runtime_supported=True,
        runtime_available=True,
        runtime_unavailable_reason="",
        credential_configured=True,
        base_url="",
        api_key="",
        registry_provider="codex",
        provider_name="codex",
        provider_native_tools=("image_gen",),
    )
    monkeypatch.setattr(
        "core.model_provider.preset_config.resolve_route_binding_candidates",
        lambda _route_key: [(candidate, SimpleNamespace(preset=preset))],
    )
    monkeypatch.setattr(
        "core.model_provider.provider_config.get_provider_instance",
        lambda _provider_id: provider,
    )
    monkeypatch.setattr(
        "nanobot_kt.codex_accounts.codex_account_pool.ordered_account_ids",
        lambda session_id: (
            "ca_second_account_02",
            "ca_first_account_001",
        ) if session_id == "session-42" else (),
    )

    plans = resolve_reply_route_plans(
        default_base_url="https://fallback.example.com/v1",
        default_api_key="fallback-secret",
        session_id="session-42",
    )

    assert [item.model for item in plans] == [
        "gpt-5.6-codex",
        "gpt-5.6-codex",
    ]
    assert [item.codex_account_id for item in plans] == [
        "ca_second_account_02",
        "ca_first_account_001",
    ]
