"""Native Reply Route 的运行时依赖边界回归。"""

from types import SimpleNamespace

from core.agent_runtime import (
    AgentRuntimeKind,
    AgentRuntimeSelectionPolicy,
)
from nanobot_kt.bridge import NanobotBridgePool
from nanobot_kt.gateway_model_profile_adapter import (
    KtGatewayModelProfileAdapter,
)
from nanobot_kt.model_runtime import resolve_reply_route_plans


def test_native_reply_route_does_not_require_optional_kt_driver_sdk(
    monkeypatch,
):
    preset = SimpleNamespace(
        id="openai-native",
        provider_id="newapi",
        enabled=True,
        timeout=120,
        model="demo-model",
        temperature=0.2,
        max_output=8192,
        max_context=128000,
        cost_input_1m=1.0,
        cost_output_1m=2.0,
        intelligence=12,
        fallback_only=False,
        input_modalities=("text",),
        output_modalities=("text",),
        reasoning_effort="",
        service_tier="",
        enable_thinking="auto",
        capabilities={"supports_stream": True, "supports_tools": True},
        extra_headers={},
        extra_body={},
        retry_policy={},
        driver_options={},
    )
    candidate = SimpleNamespace(identity="newapi/demo-model")
    provider = SimpleNamespace(
        id="newapi",
        display_name="New API",
        driver_type="openai",
        enabled=True,
        agent_runtime_supported=True,
        runtime_available=False,
        runtime_unavailable_reason="缺少 Python 依赖：openai",
        credential_configured=True,
        base_url="https://gateway.example.com/v1",
        api_key="secret",
        registry_provider="new-api",
        provider_name="newapi",
        provider_native_tools=(),
    )
    monkeypatch.setattr(
        "core.model_provider.preset_config.resolve_route_binding_candidates",
        lambda _route_key: [(candidate, SimpleNamespace(preset=preset))],
    )
    monkeypatch.setattr(
        "core.model_provider.provider_config.get_provider_instance",
        lambda _provider_id: provider,
    )

    plans = resolve_reply_route_plans(
        default_base_url="https://fallback.example.com/v1",
        default_api_key="fallback-secret",
        runtime_kind="native",
    )

    assert len(plans) == 1
    assert plans[0].profile_id == "openai-native"
    assert plans[0].driver_type == "openai"


def test_gateway_model_profiles_follow_native_runtime_kind(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "nanobot_kt.gateway_model_profile_adapter.resolve_reply_route_plans",
        lambda **kwargs: calls.append(kwargs) or [],
    )

    profiles = KtGatewayModelProfileAdapter(
        runtime_kind="native",
    ).list_profiles()

    assert profiles == ()
    assert calls[0]["runtime_kind"] == "native"


def test_bridge_pool_exposes_default_runtime_kind():
    pool = NanobotBridgePool(
        selection_policy=AgentRuntimeSelectionPolicy(
            default_kind=AgentRuntimeKind.NATIVE,
        ),
    )

    assert pool.default_runtime_kind is AgentRuntimeKind.NATIVE
