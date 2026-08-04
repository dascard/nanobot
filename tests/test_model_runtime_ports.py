"""模型决策与模型目录 Port 生命周期合同。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from core.model_provider.catalog_runtime import (
    ModelCatalogRuntime,
    ModelCatalogRuntimeState,
    ModelCatalogWriterPort,
)
from core.model_provider.decision_runtime import (
    DecisionModelPort,
    DecisionModelRuntime,
    DecisionModelRuntimeState,
)


class _DecisionPort:
    @property
    def adapter_id(self) -> str:
        return "fake_decision"

    def classify_private(
        self,
        message: str,
        has_files: bool = False,
    ) -> Mapping[str, Any]:
        return {"action": "reply_now", "message": message, "has_files": has_files}

    def judge_group_timing(self, context: str) -> Mapping[str, Any]:
        return {"action": "continue", "context": context}

    def judge_group_proactive(self, context: str) -> Mapping[str, Any]:
        return {"should_speak": True, "context": context}


class _CatalogPort:
    def __init__(self) -> None:
        self.models: list[dict[str, Any]] = []

    @property
    def adapter_id(self) -> str:
        return "fake_catalog"

    def upsert_models(self, models: tuple[Mapping[str, Any], ...]) -> int:
        self.models.extend(dict(model) for model in models)
        return len(models)


class _ChatCompletionPort:
    def __init__(self) -> None:
        self.requests = []

    @property
    def adapter_id(self) -> str:
        return "fake_chat"

    async def complete_chat(self, request):
        self.requests.append(request)
        return {"choices": [{"message": {"content": "完成"}}]}

    async def stream_chat(self, request):
        self.requests.append(request)
        yield {"choices": [{"delta": {"content": "完"}}]}
        yield {"choices": [{"delta": {"content": "成"}}]}


def test_decision_model_runtime_is_explicit_and_fail_closed():
    runtime = DecisionModelRuntime()
    port = _DecisionPort()

    assert isinstance(port, DecisionModelPort)
    assert runtime.state is DecisionModelRuntimeState.NEW
    with pytest.raises(RuntimeError, match="尚未启动"):
        runtime.judge_group_timing("上下文")

    runtime.start(port)
    assert runtime.classify_private("消息", True)["has_files"] is True
    assert runtime.judge_group_timing("上下文")["action"] == "continue"
    assert runtime.judge_group_proactive("上下文")["should_speak"] is True
    assert runtime.introspect() == {
        "state": "running",
        "adapter_id": "fake_decision",
    }

    runtime.stop()
    assert runtime.state is DecisionModelRuntimeState.STOPPED
    with pytest.raises(RuntimeError, match="已经停止"):
        runtime.classify_private("消息")


def test_model_catalog_runtime_validates_lifecycle_and_write_count():
    runtime = ModelCatalogRuntime()
    port = _CatalogPort()

    assert isinstance(port, ModelCatalogWriterPort)
    assert runtime.state is ModelCatalogRuntimeState.NEW
    with pytest.raises(RuntimeError, match="尚未启动"):
        runtime.upsert_models(({"id": "model-a"},))

    runtime.start(port)
    assert runtime.upsert_models(({"id": "model-a"}, {"id": "model-b"})) == 2
    assert port.models == [{"id": "model-a"}, {"id": "model-b"}]

    runtime.stop()
    assert runtime.state is ModelCatalogRuntimeState.STOPPED


@pytest.mark.asyncio
async def test_runtime_bound_chat_completion_port_fails_fast_and_forwards_calls():
    from core.model_provider.chat_runtime import (
        ChatCompletionRequest,
        ChatCompletionRuntimeUnavailableError,
        RuntimeBoundChatCompletionPort,
        start_chat_completion_runtime,
        stop_chat_completion_runtime,
    )

    stop_chat_completion_runtime()
    bound = RuntimeBoundChatCompletionPort()
    request = ChatCompletionRequest(messages=({"role": "user", "content": "你好"},))
    with pytest.raises(ChatCompletionRuntimeUnavailableError, match="尚未就绪"):
        bound.ensure_ready()
    with pytest.raises(ChatCompletionRuntimeUnavailableError, match="已经停止"):
        await bound.complete_chat(request)

    upstream = _ChatCompletionPort()
    try:
        start_chat_completion_runtime(upstream)
        bound.ensure_ready()
        assert bound.adapter_id == "chat-runtime:fake_chat"
        assert (await bound.complete_chat(request))["choices"][0]["message"][
            "content"
        ] == "完成"
        chunks = [chunk async for chunk in bound.stream_chat(request)]
        assert [chunk["choices"][0]["delta"]["content"] for chunk in chunks] == [
            "完",
            "成",
        ]
        assert upstream.requests == [request, request]
    finally:
        stop_chat_completion_runtime()


def test_reply_route_plan_is_owned_by_core_and_kt_keeps_compatibility_alias():
    from core.model_provider import ReplyRoutePlan
    from nanobot_kt.model_runtime import ReplyRoutePlan as KtReplyRoutePlan

    plan = ReplyRoutePlan(
        provider_id="provider-a",
        registry_provider="openai",
        base_url="https://example.invalid/v1",
        api_key="secret",
        timeout=30,
    )

    assert KtReplyRoutePlan is ReplyRoutePlan
    assert plan.provider_id == "provider-a"


def test_variation_patch_validation_is_framework_independent_and_fail_closed():
    from core.model_provider.preset_config import validate_variation_patch_map

    validate_variation_patch_map(
        {
            "temperature": 0.2,
            "extra_body.reasoning.effort": "high",
        }
    )

    with pytest.raises(ValueError, match="不支持"):
        validate_variation_patch_map({"api_key": "forbidden"})
    with pytest.raises(ValueError, match="路径冲突"):
        validate_variation_patch_map(
            {
                "extra_body": "not-an-object",
                "extra_body.reasoning": True,
            }
        )


def test_registry_provider_resolution_no_longer_depends_on_bridge(monkeypatch):
    from clients import classifier_client

    monkeypatch.setattr(
        classifier_client,
        "_get_provider_config",
        lambda provider_id: (
            {"registry_provider": "anthropic"} if provider_id == "provider-a" else None
        ),
    )

    assert classifier_client.registry_provider_for_route("") == "new-api"
    assert classifier_client.registry_provider_for_route("newapi") == "new-api"
    assert classifier_client.registry_provider_for_route("provider-a") == "anthropic"
    assert classifier_client.registry_provider_for_route("provider-b") == "provider-b"


@pytest.mark.asyncio
async def test_model_provider_admin_runtime_uses_separate_catalog_and_probe_ports():
    from core.model_provider.admin_runtime import (
        ModelPresetProbeResult,
        ModelProviderAdminRuntimeUnavailableError,
        CodexAdminPort,
        NativeToolCatalogPort,
        PresetConnectivityPort,
        list_provider_native_tools,
        probe_model_preset,
        start_model_provider_admin_runtime,
        stop_model_provider_admin_runtime,
    )
    from core.model_provider.route_plan import ReplyRoutePlan

    class AdminAdapter:
        def list_native_tools(self):
            return ({"name": "image_gen"},)

        async def probe_preset(self, plan, *, prompt):
            return ModelPresetProbeResult(
                content=f"{plan.model}:{prompt}",
                usage={"output_tokens": 1},
            )

    class CodexAdapter:
        def status(self):
            return {"authenticated": False}

        def list_accounts(self, database):
            del database
            return ()

        def update_account(
            self,
            account_id,
            *,
            name,
            enabled,
            weight,
            database,
        ):
            del name, enabled, weight, database
            return {"id": account_id}

        def delete_account(self, account_id, *, database):
            del account_id, database
            return True

        async def start_device_login(
            self,
            *,
            account_id,
            name,
            database,
        ):
            del name, database
            return {"account_id": account_id, "status": "pending"}

        async def get_device_login(self, login_id):
            return {"login_id": login_id}

        async def usage(self):
            return {"status": "no_data_yet"}

    adapter = AdminAdapter()
    codex_adapter = CodexAdapter()
    assert isinstance(adapter, NativeToolCatalogPort)
    assert isinstance(adapter, PresetConnectivityPort)
    assert isinstance(codex_adapter, CodexAdminPort)
    stop_model_provider_admin_runtime()
    try:
        with pytest.raises(ModelProviderAdminRuntimeUnavailableError):
            list_provider_native_tools()
        start_model_provider_admin_runtime(
            native_tools=adapter,
            connectivity=adapter,
            codex_admin=codex_adapter,
        )
        tools = list_provider_native_tools()
        assert [dict(item) for item in tools] == [{"name": "image_gen"}]
        result = await probe_model_preset(
            ReplyRoutePlan(
                provider_id="provider-a",
                registry_provider="openai",
                base_url="https://example.invalid/v1",
                api_key="secret",
                timeout=30,
                model="model-a",
            ),
            prompt="ping",
        )
        assert result.content == "model-a:ping"
        assert dict(result.usage) == {"output_tokens": 1}
    finally:
        stop_model_provider_admin_runtime()
