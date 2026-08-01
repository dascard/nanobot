"""模型 Provider Port、Adapter 与 Registry 契约测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clients.provider_adapter import (
    OpenAICompatibleProviderAdapter,
    registry_from_provider_configs,
)
from core.model_provider import (
    AsyncModelCompletionPort,
    DuplicateProviderError,
    ModelProviderRegistry,
    ModelProviderRequest,
    ModelProviderResponse,
    OverridePolicy,
    ProviderAvailability,
    ProviderCapability,
    ProviderCapabilityError,
    ProviderDescriptor,
    ProviderRegistryFrozenError,
    ProviderUnavailableError,
    SyncModelCompletionPort,
)


class _FakeProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        aliases: tuple[str, ...] = (),
        capabilities: frozenset[ProviderCapability] | None = None,
        available: bool = True,
    ) -> None:
        self._descriptor = ProviderDescriptor(
            id=provider_id,
            display_name=provider_id,
            aliases=aliases,
            capabilities=capabilities
            or frozenset({ProviderCapability.CHAT_COMPLETION}),
        )
        self._available = available

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def availability(self) -> ProviderAvailability:
        return ProviderAvailability(
            available=self._available,
            configured=True,
            reason_code="configured" if self._available else "disabled",
        )

    def complete(self, request: ModelProviderRequest) -> ModelProviderResponse:
        return ModelProviderResponse(content=str(request.messages[-1]["content"]))

    def introspect(self):
        return {
            **self.descriptor.metadata(),
            **self.availability().metadata(),
        }


def test_registry_rejects_duplicates_requires_authorized_override_and_freezes():
    registry = ModelProviderRegistry()
    original = _FakeProvider("newapi", aliases=("new_api",))
    replacement = _FakeProvider("newapi")
    registry.register(original)

    with pytest.raises(DuplicateProviderError, match="名称冲突"):
        registry.register(replacement)
    with pytest.raises(DuplicateProviderError, match="operator"):
        registry.register(
            replacement,
            override_policy=OverridePolicy.REPLACE,
        )

    registry.register(
        replacement,
        override_policy=OverridePolicy.REPLACE,
        operator_authorized=True,
    )
    registry.freeze()

    assert registry.require("newapi") is replacement
    assert registry.get("new_api") is None
    with pytest.raises(ProviderRegistryFrozenError):
        registry.register(_FakeProvider("another"))
    with pytest.raises(TypeError):
        registry.providers()["injected"] = replacement


def test_registry_enforces_capability_and_availability():
    registry = ModelProviderRegistry()
    registry.register(_FakeProvider("disabled", available=False))
    registry.freeze()

    with pytest.raises(ProviderUnavailableError):
        registry.require("disabled")
    with pytest.raises(ProviderCapabilityError):
        registry.require(
            "disabled",
            capabilities=frozenset({ProviderCapability.VISION}),
            require_available=False,
        )


def test_config_catalog_rejects_alias_collision():
    with pytest.raises(DuplicateProviderError):
        registry_from_provider_configs([
            {
                "id": "provider_a",
                "base_url": "http://provider-a.test/v1",
                "legacy_aliases": ["shared_alias"],
            },
            {
                "id": "provider_b",
                "base_url": "http://provider-b.test/v1",
                "legacy_aliases": ["shared_alias"],
            },
        ])


def test_openai_adapter_implements_port_and_introspection_redacts_secrets(monkeypatch):
    response = SimpleNamespace(
        status=200,
        read=lambda *_args: json.dumps({
            "choices": [{
                "message": {
                    "content": "<think>隐藏</think>结果",
                    "reasoning_content": "推理",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 8},
        }).encode("utf-8"),
        getcode=lambda: 200,
        __enter__=lambda self: self,
        __exit__=lambda *_args: False,
    )

    class _Response:
        status = response.status

        def read(self, *_args):
            return response.read()

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    calls = []

    class _Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return _Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: _Opener())
    adapter = OpenAICompatibleProviderAdapter(
        descriptor=ProviderDescriptor(
            id="newapi",
            display_name="New API",
            capabilities=frozenset({
                ProviderCapability.CHAT_COMPLETION,
                ProviderCapability.REASONING_CONTENT,
            }),
        ),
        base_url="http://internal-provider.test/v1",
        api_key="provider-secret",
    )

    assert isinstance(adapter, SyncModelCompletionPort)

    result = adapter.complete(ModelProviderRequest(
        messages=({"role": "user", "content": "你好"},),
        model="model-a",
        max_tokens=64,
        timeout_seconds=7,
        enable_thinking="false",
        reasoning_effort="high",
        service_tier="priority",
        extra_headers={"X-Nanobot-Route": "test"},
        extra_body={"parallel_tool_calls": True},
    ))
    introspection = dict(adapter.introspect())
    payload = json.loads(calls[0][0].data.decode("utf-8"))

    assert result.content == "结果"
    assert result.reasoning_content == "推理"
    assert result.finish_reason == "stop"
    assert result.usage == {"total_tokens": 8}
    assert calls[0][1] == 7
    assert payload["reasoning_effort"] == "high"
    assert payload["service_tier"] == "priority"
    assert payload["parallel_tool_calls"] is True
    assert calls[0][0].get_header("X-nanobot-route") == "test"
    assert introspection["authentication_configured"] is True
    serialized = json.dumps(introspection, ensure_ascii=False)
    assert "provider-secret" not in serialized
    assert "internal-provider.test" not in serialized


@pytest.mark.asyncio
async def test_new_api_client_implements_async_compatibility_port(monkeypatch):
    from clients.new_api_client import NewAPIClient

    client = NewAPIClient(
        api_key="new-api-secret",
        base_url="http://new-api.internal/v1",
    )

    async def fake_chat_completion(**kwargs):
        assert kwargs["manual_model"] == "model-a"
        assert kwargs["llm_source"] == "chat.reply"
        return {
            "choices": [{
                "message": {
                    "content": "完成",
                    "reasoning_content": "推理",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 9},
        }

    monkeypatch.setattr(client, "chat_completion", fake_chat_completion)

    assert isinstance(client, AsyncModelCompletionPort)
    result = await client.complete_async(ModelProviderRequest(
        messages=({"role": "user", "content": "测试"},),
        model="model-a",
        trace_source="chat.reply",
    ))

    assert result.content == "完成"
    assert result.reasoning_content == "推理"
    assert result.usage == {"total_tokens": 9}
    introspection = json.dumps(client.introspect(), ensure_ascii=False)
    assert "new-api-secret" not in introspection
    assert "new-api.internal" not in introspection


def test_dynamic_provider_instance_is_discovered_and_public_view_redacts_key(
    db_session,
):
    from clients.classifier_client import _get_provider_config, list_providers
    from core.database import SystemSetting
    from core.model_provider.provider_config import get_provider_instance

    values = {
        "model.providers.team_gateway.display_name": "团队网关",
        "model.providers.team_gateway.driver_type": "openai",
        "model.providers.team_gateway.base_url": "http://team-gateway.test/v1",
        "model.providers.team_gateway.api_key": "dynamic-provider-secret",
        "model.providers.team_gateway.enabled": "1",
        "model.providers.team_gateway.registry_provider": "team-gateway",
        "model.providers.team_gateway.model_discovery_enabled": "1",
    }
    db_session.add_all([
        SystemSetting(key=key, value=value)
        for key, value in values.items()
    ])
    db_session.commit()

    instance = get_provider_instance("team_gateway", db_session)
    assert instance is not None
    assert instance.api_key == "dynamic-provider-secret"
    assert instance.route_completion_supported is True
    assert instance.public_view()["api_key_configured"] is True
    assert "dynamic-provider-secret" not in json.dumps(
        instance.public_view(),
        ensure_ascii=False,
    )

    runtime = _get_provider_config("team_gateway")
    assert runtime is not None
    assert runtime["api_key"] == "dynamic-provider-secret"
    assert any(
        provider["id"] == "team_gateway"
        for provider in list_providers(db_session)
    )


def test_provider_driver_capabilities_do_not_overpromise_route_support(
    db_session,
):
    from core.database import SystemSetting
    from core.model_provider.provider_config import get_provider_instance

    db_session.add_all([
        SystemSetting(
            key="model.providers.anthropic_native.driver_type",
            value="anthropic",
        ),
        SystemSetting(
            key="model.providers.anthropic_native.base_url",
            value="https://api.anthropic.com",
        ),
    ])
    db_session.commit()

    provider = get_provider_instance("anthropic_native", db_session)
    assert provider is not None
    public = provider.public_view()
    assert public["kt_driver_available"] is True
    assert public["route_completion_supported"] is False
    assert public["agent_runtime_supported"] is True
    assert isinstance(public["runtime_available"], bool)
    assert isinstance(public["runtime_unavailable_reason"], str)
    assert public["model_discovery_supported"] is False


def test_provider_catalog_error_does_not_echo_configured_api_key():
    from clients.provider_catalog import discover_provider_models

    class _FailingOpener:
        def open(self, _request, timeout=None):
            assert timeout == 10
            raise RuntimeError("上游错误回显 catalog-secret")

    with pytest.raises(RuntimeError) as captured:
        discover_provider_models(
            {
                "driver_type": "openai",
                "base_url": "http://provider.test/v1",
                "api_key": "catalog-secret",
            },
            opener_factory=lambda *_args: _FailingOpener(),
        )

    assert "catalog-secret" not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)
