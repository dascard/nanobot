"""Tests for the new model routing system."""
import asyncio
import pytest


@pytest.fixture(autouse=True)
def _isolate_model_failure_state(monkeypatch, tmp_path):
    import clients.model_registry as model_registry

    monkeypatch.setattr(
        model_registry,
        "_FAILURE_STATE_PATH",
        str(tmp_path / "model_failures.json"),
    )


class TestClassifierRouteProviderResolution:
    def test_provider_setting_base_url_precedes_builtin_fallback_without_env_url(
        self,
        monkeypatch,
    ):
        from clients.classifier_client import _get_provider_config

        configured_url = "http://newapi:9000/v1"
        values = {
            "model.providers.newapi.base_url": configured_url,
            "model.providers.newapi.api_key": "provider-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.delenv("NEW_API_BASE_URL", raising=False)
        monkeypatch.setattr(
            "config.NEW_API_BASE_URL",
            "https://api.new-api.com/v1",
        )
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        provider = _get_provider_config("newapi")

        assert provider is not None
        assert provider["base_url"] == configured_url

    def test_provider_enabled_string_false_is_disabled(self, monkeypatch):
        from clients.classifier_client import _get_provider_config

        values = {
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "key",
            "model.providers.newapi.enabled": "false",
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        provider = _get_provider_config("newapi")

        assert provider is not None
        assert provider["enabled"] is False

    def test_route_provider_uses_provider_base_url_when_route_base_url_not_explicit(self, monkeypatch):
        from clients.classifier_client import _resolve_classifier_route

        values = {
            "model.route.timing_gate.provider": "local_qwen",
            "model.providers.local_qwen.base_url": "http://local-qwen:9999/v1",
            "model.providers.local_qwen.enabled": True,
            "model.route.sticker_describe.provider": "newapi",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = _resolve_classifier_route("sticker_describe")

        assert route["provider_id"] == "newapi"
        assert route["base_url"] == "http://newapi:9000/v1"
        assert route["api_key"] == "newapi-key"

    def test_explicit_route_base_url_overrides_provider_base_url(self, monkeypatch):
        from clients.classifier_client import _resolve_classifier_route

        values = {
            "model.route.timing_gate.provider": "local_qwen",
            "model.providers.local_qwen.base_url": "http://local-qwen:9999/v1",
            "model.providers.local_qwen.enabled": True,
            "model.route.sticker_describe.provider": "newapi",
            "model.route.sticker_describe.base_url": "http://route-override:9001/v1",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = _resolve_classifier_route("sticker_describe")

        assert route["provider_id"] == "newapi"
        assert route["base_url"] == "http://route-override:9001/v1"
        assert route["api_key"] == "newapi-key"

    def test_route_api_key_overrides_provider_api_key(self, monkeypatch):
        from clients.classifier_client import _resolve_classifier_route

        values = {
            "model.route.timing_gate.provider": "local_qwen",
            "model.providers.local_qwen.base_url": "http://local-qwen:9999/v1",
            "model.providers.local_qwen.enabled": True,
            "model.route.sticker_describe.provider": "newapi",
            "model.route.sticker_describe.api_key": "route-key",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "provider-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = _resolve_classifier_route("sticker_describe")

        assert route["provider_id"] == "newapi"
        assert route["base_url"] == "http://newapi:9000/v1"
        assert route["api_key"] == "route-key"

    def test_resolve_model_route_preserves_inherited_provider_id(self, monkeypatch):
        from clients.classifier_client import resolve_model_route

        values = {
            "model.route.timing_gate.provider": "newapi",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
            "model.route.private_decision.provider": "",
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = resolve_model_route("private_decision")

        assert route["provider_id"] == "newapi"
        assert route["base_url"] == "http://newapi:9000/v1"
        assert route["api_key"] == "newapi-key"

    def test_reply_model_does_not_inherit_timing_gate_model(self, monkeypatch):
        from clients.classifier_client import resolve_model_route

        values = {
            "model.route.timing_gate.provider": "local_llama",
            "model.route.timing_gate.model": "timing-model",
            "model.providers.local_llama.base_url": "http://local-llama:9999/v1",
            "model.providers.local_llama.enabled": True,
            "model.route.reply.provider": "newapi",
            "model.reply": "selected-reply-model",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = resolve_model_route("reply")

        assert route["provider_id"] == "newapi"
        assert route["model"] == "selected-reply-model"

    def test_session_summary_prefers_dedicated_model_setting(self, monkeypatch):
        from clients.classifier_client import resolve_model_route

        values = {
            "model.session_summary": "dedicated-summary-model",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = resolve_model_route("session_summary")

        assert route["provider_id"] == "newapi"
        assert route["model"] == "dedicated-summary-model"

    def test_session_summary_falls_back_to_registered_fast_model(
        self,
        monkeypatch,
    ):
        from clients.classifier_client import resolve_model_route

        values = {
            "model.fast": "registered-fast-model",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = resolve_model_route("session_summary")

        assert route["provider_id"] == "newapi"
        assert route["model"] == "registered-fast-model"

    def test_timing_proactive_inherits_reply_route_config(self, monkeypatch):
        from clients.classifier_client import resolve_model_route

        values = {
            "model.route.timing_gate.provider": "local_llama",
            "model.route.timing_gate.model": "timing-model",
            "model.route.timing_gate.max_tokens": 30,
            "model.route.timing_gate.temperature": 0,
            "model.route.timing_gate.timeout": 5,
            "model.providers.local_llama.base_url": "http://local-llama:9999/v1",
            "model.providers.local_llama.enabled": True,
            "model.route.reply.provider": "newapi",
            "model.route.reply.model": "reply-route-model",
            "model.route.reply.max_tokens": 2048,
            "model.route.reply.temperature": 0.7,
            "model.route.reply.timeout": 60,
            "model.route.reply.enable_thinking": "true",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = resolve_model_route("timing_proactive")

        assert route["provider_id"] == "newapi"
        assert route["base_url"] == "http://newapi:9000/v1"
        assert route["api_key"] == "newapi-key"
        assert route["model"] == "reply-route-model"
        assert route["max_tokens"] == 65536
        assert route["temperature"] == 0.0
        assert route["timeout"] == 30
        assert route["enable_thinking"] == "true"
        assert route["inherited_from"] == "reply"
        assert route["source"] == "inherited_from_reply"

    def test_timing_proactive_inherits_legacy_reply_model_setting(self, monkeypatch):
        from clients.classifier_client import _resolve_classifier_route

        values = {
            "model.reply": "legacy-main-reply-model",
            "model.route.reply.provider": "newapi",
            "model.route.reply.max_tokens": 2048,
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        route = _resolve_classifier_route("timing_proactive")

        assert route["provider_id"] == "newapi"
        assert route["model"] == "legacy-main-reply-model"
        assert route["max_tokens"] == 65536

    def test_route_enable_thinking_inherits_and_overrides(self, monkeypatch):
        from clients.classifier_client import resolve_model_route

        values = {
            "model.route.timing_gate.provider": "newapi",
            "model.route.timing_gate.enable_thinking": "false",
            "model.providers.newapi.base_url": "http://newapi:9000/v1",
            "model.providers.newapi.api_key": "newapi-key",
            "model.providers.newapi.enabled": True,
            "model.route.private_decision.provider": "",
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        inherited = resolve_model_route("private_decision")
        assert inherited["enable_thinking"] == "false"

        values["model.route.private_decision.enable_thinking"] = "true"
        overridden = resolve_model_route("private_decision")
        assert overridden["enable_thinking"] == "true"
        assert overridden["overridden_fields"]["enable_thinking"] == "true"

    def test_call_model_route_rejects_disabled_provider(self, monkeypatch):
        from clients.classifier_client import call_model_route

        values = {
            "model.route.timing_gate.provider": "local_qwen",
            "model.providers.local_qwen.base_url": "http://local-qwen:9999/v1",
            "model.providers.local_qwen.enabled": False,
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        with pytest.raises(RuntimeError, match="provider disabled: local_llama"):
            call_model_route(route_key="timing_gate", user_message="ping")

    @pytest.mark.parametrize("route_key", ["timing_gate", "classifier_legacy"])
    def test_call_model_route_uses_v2_task_template_for_classifier_routes(
        self, route_key, tmp_path, monkeypatch
    ):
        import json

        from clients.classifier_client import call_model_route

        default_dir = tmp_path / "prompts_v2"
        task_path = default_dir / "tasks" / f"{route_key}.md"
        task_path.parent.mkdir(parents=True)
        task_body = {
            "timing_gate": "V2 判定: {{ pending_text }} / {{ bot_name }}",
            "private_decision": "V2 私聊判定: {{ message }}",
            "classifier_legacy": "V2 兼容: {{ system_prompt }} / {{ message }}",
        }[route_key]
        task_path.write_text(
            f"""---
name: Timing Gate
version: 1
kind: task
tool_name: {route_key}
---
{task_body}
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("NANOBOT_PROMPT_V2_DIR", str(default_dir))
        monkeypatch.setenv("NANOBOT_PROMPT_V2_RUNTIME_DIR", str(tmp_path / "runtime_v2"))

        values = {
            "model.route.timing_gate.base_url": "http://local-test/v1",
            "model.route.timing_gate.model": "unit-model",
            "model.route.timing_gate.max_tokens": 80,
            "model.route.timing_gate.temperature": 0,
            "model.route.timing_gate.timeout": 5,
            "model.route.timing_gate.enable_thinking": "false",
        }
        monkeypatch.setattr(
            "core.settings_service.settings.get",
            lambda key, default=None: values.get(key, default),
        )

        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({
                    "choices": [{"message": {"content": "ok"}}],
                }).encode("utf-8")

        class FakeOpener:
            def open(self, req, timeout=0):
                captured["payload"] = json.loads(req.data.decode("utf-8"))
                return FakeResponse()

        monkeypatch.setattr(
            "urllib.request.build_opener",
            lambda *_args, **_kwargs: FakeOpener(),
        )

        assert call_model_route(
            route_key=route_key,
            system_prompt="legacy system",
            user_message="ping",
        ) == "ok"

        messages = captured["payload"]["messages"]
        if route_key in {"timing_gate", "classifier_legacy"}:
            assert "ping" not in messages[0]["content"]
            assert "TaskPayload" in messages[0]["content"]
            assert messages[1] == {"role": "user", "content": "ping"}
            assert sum("ping" in str(message.get("content") or "") for message in messages) == 1
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[1]["content"] == "ping"
        if route_key == "timing_gate":
            assert "legacy system" not in json.dumps(messages, ensure_ascii=False)
        assert captured["payload"]["enable_thinking"] is False
        assert captured["payload"]["thinking"] == {"type": "disabled"}


class TestNewAPIBackgroundTasks:
    @pytest.mark.asyncio
    async def test_background_task_is_kept_until_done(self):
        from clients.new_api_client import NewAPIClient

        NewAPIClient._background_tasks.clear()
        release = asyncio.Event()

        async def wait_for_release():
            await release.wait()

        task = NewAPIClient._track_background_task(wait_for_release(), label="unit_wait")
        try:
            await asyncio.sleep(0)
            assert task in NewAPIClient._background_tasks
            release.set()
            await task
            await asyncio.sleep(0)
            assert task not in NewAPIClient._background_tasks
        finally:
            NewAPIClient._background_tasks.clear()

    @pytest.mark.asyncio
    async def test_background_task_exception_is_observed_and_discarded(self, caplog):
        from clients.new_api_client import NewAPIClient

        NewAPIClient._background_tasks.clear()

        async def fail():
            raise RuntimeError("tracker failed")

        with caplog.at_level("WARNING", logger="nanobot.new_api"):
            task = NewAPIClient._track_background_task(fail(), label="unit_fail")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        assert task.done()
        assert task not in NewAPIClient._background_tasks
        assert "unit_fail" in caplog.text
        assert "tracker failed" in caplog.text


class TestNewAPIModelSync:
    @staticmethod
    def _discard_background_task(awaitable, **_kwargs):
        awaitable.close()
        return None

    @pytest.mark.asyncio
    async def test_empty_registry_ignores_recent_persisted_timestamp(
        self,
        monkeypatch,
    ):
        from unittest.mock import AsyncMock

        from clients import model_registry, new_api_client as module
        from clients.new_api_client import NewAPIClient

        class FakeRegistry:
            def __init__(self):
                self.models = []

            def get_models_by_provider(self, provider):
                assert provider == "new-api"
                return list(self.models)

            def replace_provider_models(self, provider, models):
                assert provider == "new-api"
                self.models = list(models)
                return len(models)

        fake_registry = FakeRegistry()
        fetch_models = AsyncMock(return_value=[{
            "id": "live-model",
            "provider": "new-api",
        }])
        monkeypatch.setattr(module, "registry", fake_registry)
        monkeypatch.setattr(
            model_registry.runtime_state,
            "get",
            AsyncMock(return_value=module.time.time()),
        )
        monkeypatch.setattr(NewAPIClient, "_last_model_sync_ts", None)
        monkeypatch.setattr(NewAPIClient, "_model_sync_lock", asyncio.Lock())
        monkeypatch.setattr(
            NewAPIClient,
            "_track_background_task",
            staticmethod(self._discard_background_task),
        )

        client = NewAPIClient(api_key="test", base_url="http://test")
        monkeypatch.setattr(client, "fetch_models", fetch_models)

        assert await client.sync_models_to_registry() == 1
        assert await client.sync_models_to_registry() == 0
        fetch_models.assert_awaited_once()
        assert [m["id"] for m in fake_registry.models] == ["live-model"]

    @pytest.mark.asyncio
    async def test_empty_registry_throttles_after_failed_fetch(
        self,
        monkeypatch,
    ):
        from unittest.mock import AsyncMock, MagicMock

        from clients import model_registry, new_api_client as module
        from clients.new_api_client import NewAPIClient

        fake_registry = MagicMock()
        fake_registry.get_models_by_provider.return_value = []
        fetch_models = AsyncMock(return_value=[])
        monkeypatch.setattr(module, "registry", fake_registry)
        monkeypatch.setattr(
            model_registry.runtime_state,
            "get",
            AsyncMock(return_value=module.time.time()),
        )
        monkeypatch.setattr(NewAPIClient, "_last_model_sync_ts", None)
        monkeypatch.setattr(NewAPIClient, "_model_sync_lock", asyncio.Lock())

        client = NewAPIClient(api_key="test", base_url="http://test")
        monkeypatch.setattr(client, "fetch_models", fetch_models)

        assert await client.sync_models_to_registry() == 0
        assert await client.sync_models_to_registry() == 0
        fetch_models.assert_awaited_once()
        fake_registry.replace_provider_models.assert_not_called()


class TestComplexityEstimator:
    def test_simple_greeting(self):
        from clients.new_api_client import NewAPIClient
        client = NewAPIClient(api_key="test", base_url="http://test")
        c = client.estimate_complexity([{"role": "user", "content": "你好"}])
        assert c <= 2, f"Simple greeting should have low complexity, got {c}"

    def test_code_query(self):
        from clients.new_api_client import NewAPIClient
        client = NewAPIClient(api_key="test", base_url="http://test")
        c = client.estimate_complexity(
            [{"role": "user", "content": "帮我写一个 Python 脚本来分析 sql 查询"}],
            tools=[{}],
        )
        assert c >= 4, f"Coding query with tools should have higher complexity, got {c}"

    def test_long_reasoning_query(self):
        from clients.new_api_client import NewAPIClient
        client = NewAPIClient(api_key="test", base_url="http://test")
        c = client.estimate_complexity(
            [{"role": "user", "content": "设计" * 100}],  # ~200 chars → no length bump
            tools=[{}],
        )
        assert c >= 3, f"Design marker + tools should bump complexity, got {c}"

    def test_empty_default(self):
        from clients.new_api_client import NewAPIClient
        client = NewAPIClient(api_key="test", base_url="http://test")
        c = client.estimate_complexity([])
        assert c == 2, f"Empty messages should default to baseline 2, got {c}"


class TestPriorityScore:
    def test_resolve_model_does_not_select_circuit_disabled_manual_model(self, monkeypatch):
        from unittest.mock import MagicMock

        from clients.new_api_client import NewAPIClient

        client = NewAPIClient(
            api_key="test",
            base_url="http://test",
            registry_provider="new-api",
        )
        tracker = MagicMock()
        tracker.sync_is_disabled.return_value = True
        ordered_candidates = MagicMock(return_value=[{"id": "healthy-model"}])
        monkeypatch.setattr(client, "_safe_get_failure_tracker", lambda: tracker)
        monkeypatch.setattr(client, "estimate_complexity", lambda messages, tools: 3)
        monkeypatch.setattr(client, "get_ordered_candidates", ordered_candidates)

        selected = client.resolve_model(
            messages=[{"role": "user", "content": "你好"}],
            manual_model="preferred-model",
        )

        assert selected == "healthy-model"
        tracker.sync_is_disabled.assert_called_once_with("preferred-model")
        ordered_candidates.assert_called_once()

    @pytest.mark.parametrize(
        "remaining_model",
        [
            {
                "id": "disabled-fallback",
                "enabled": False,
                "cost_input_1m": 0.01,
            },
            {
                "id": "circuit-disabled-fallback",
                "enabled": True,
                "cost_input_1m": 0.01,
            },
        ],
        ids=["disabled", "circuit-disabled"],
    )
    def test_resolve_model_does_not_use_unhealthy_raw_registry_fallback(
        self,
        monkeypatch,
        remaining_model,
    ):
        from unittest.mock import MagicMock

        from clients import new_api_client as module
        from clients.new_api_client import NewAPIClient

        client = NewAPIClient(
            api_key="test",
            base_url="http://test",
            registry_provider="new-api",
        )
        tracker = MagicMock()
        tracker.sync_is_disabled.side_effect = lambda model_id: model_id in {
            "preferred-model",
            "circuit-disabled-fallback",
        }
        monkeypatch.setattr(client, "_safe_get_failure_tracker", lambda: tracker)
        monkeypatch.setattr(client, "estimate_complexity", lambda messages, tools: 3)
        monkeypatch.setattr(client, "get_ordered_candidates", lambda **kwargs: [])

        registry = MagicMock()
        registry.get_models_by_provider.return_value = [remaining_model]
        monkeypatch.setattr(module, "registry", registry)

        selected = client.resolve_model(
            messages=[{"role": "user", "content": "你好"}],
            manual_model="preferred-model",
        )

        assert selected == ""
        registry.get_models_by_provider.assert_not_called()

    def test_priority_score_treats_none_cost_as_unknown(self):
        from clients.model_registry import ModelRegistry
        unknown = {"id": "unknown", "cost_input_1m": None, "intelligence": 8, "tags": []}
        known = {"id": "known", "cost_input_1m": 0.2, "intelligence": 8, "tags": []}
        assert ModelRegistry.compute_priority_score(known) < ModelRegistry.compute_priority_score(unknown)

    def test_free_ranks_higher_than_paid_same_intel(self):
        from clients.model_registry import ModelRegistry
        free = {"id": "a", "cost_input_1m": 0.0, "intelligence": 8, "tags": ["free", "general"]}
        paid = {"id": "b", "cost_input_1m": 0.14, "intelligence": 8, "tags": ["general"]}
        assert ModelRegistry.compute_priority_score(free) < ModelRegistry.compute_priority_score(paid)

    def test_higher_intel_ranks_above_lower_same_cost(self):
        from clients.model_registry import ModelRegistry
        a = {"id": "a", "cost_input_1m": 0.0, "intelligence": 10, "tags": ["free"]}
        b = {"id": "b", "cost_input_1m": 0.0, "intelligence": 6, "tags": ["free"]}
        assert ModelRegistry.compute_priority_score(a) < ModelRegistry.compute_priority_score(b)

    def test_unstable_penalty(self):
        from clients.model_registry import ModelRegistry
        stable = {"id": "a", "cost_input_1m": 0.0, "intelligence": 8, "tags": ["free"]}
        unstable = {"id": "b", "cost_input_1m": 0.0, "intelligence": 8, "tags": ["free", "unstable"]}
        assert ModelRegistry.compute_priority_score(stable) < ModelRegistry.compute_priority_score(unstable)

    def test_expensive_sinks(self):
        from clients.model_registry import ModelRegistry
        cheap = {"id": "a", "cost_input_1m": 0.04, "intelligence": 7, "tags": []}
        expensive = {"id": "b", "cost_input_1m": 0.43, "intelligence": 7, "tags": []}
        assert ModelRegistry.compute_priority_score(cheap) < ModelRegistry.compute_priority_score(expensive)

    def test_ordered_candidates_handles_none_cost_and_keeps_floor_first(self, monkeypatch):
        from clients import new_api_client as module
        from clients.new_api_client import NewAPIClient

        class FakeRegistry:
            def get_models_by_provider(self, provider):
                assert provider == "x"
                return [
                    {
                        "id": "below-free",
                        "provider": "x",
                        "intelligence": 6,
                        "cost_input_1m": 0.0,
                        "tags": ["free"],
                    },
                    {
                        "id": "qualified-known",
                        "provider": "x",
                        "intelligence": 8,
                        "cost_input_1m": 0.2,
                        "tags": [],
                    },
                    {
                        "id": "qualified-null",
                        "provider": "x",
                        "intelligence": 9,
                        "cost_input_1m": None,
                        "tags": [],
                    },
                ]

            def compute_priority_score(self, model):
                from clients.model_registry import ModelRegistry
                return ModelRegistry.compute_priority_score(model)

        monkeypatch.setattr(module, "registry", FakeRegistry())
        monkeypatch.setattr(NewAPIClient, "_failure_tracker", None)
        monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

        client = NewAPIClient(api_key="test", base_url="http://test")
        ids = [
            item["id"]
            for item in client.get_ordered_candidates("x", intel_floor=8, max_cost=1.0)
        ]

        assert ids == ["qualified-known", "below-free"]

    def test_model_override_null_cost_keeps_base_cost(self, monkeypatch):
        from clients.new_api_client import NewAPIClient

        monkeypatch.setattr(
            NewAPIClient,
            "_model_overrides_cache",
            {"paid-model": {"cost_input_1m": None, "cost_output_1m": None}},
        )

        client = NewAPIClient(api_key="test", base_url="http://test")
        merged = client._apply_model_override(
            "paid-model",
            {
                "id": "paid-model",
                "tags": ["paid"],
                "cost_input_1m": 0.2,
                "cost_output_1m": 0.8,
                "description": "base",
            },
        )

        assert merged["cost_input_1m"] == 0.2
        assert merged["cost_output_1m"] == 0.8

    def test_model_override_null_capability_keeps_base_capability(self, monkeypatch):
        from clients.new_api_client import NewAPIClient

        monkeypatch.setattr(
            NewAPIClient,
            "_model_overrides_cache",
            {"vision-model": {"supports_image": None}},
        )

        client = NewAPIClient(api_key="test", base_url="http://test")
        merged = client._apply_model_override(
            "vision-model",
            {
                "id": "vision-model",
                "tags": ["vision"],
                "supports_image": True,
                "supports_tools": True,
                "supports_stream": True,
                "cost_input_1m": 0.0,
                "cost_output_1m": 0.0,
                "description": "base",
            },
        )

        assert merged["supports_image"] is True

    def test_model_override_preserves_explicit_description(self, monkeypatch):
        from clients.new_api_client import NewAPIClient

        monkeypatch.setattr(
            NewAPIClient,
            "_model_overrides_cache",
            {
                "official-model": {
                    "description": "经官方资料核验的描述",
                    "tags": ["paid", "vision"],
                },
            },
        )

        client = NewAPIClient(api_key="test", base_url="http://test")
        merged = client._apply_model_override(
            "official-model",
            {
                "id": "official-model",
                "tags": ["paid"],
                "cost_input_1m": 0.2,
                "cost_output_1m": 0.8,
                "description": "自动生成描述",
            },
        )

        assert merged["description"] == "经官方资料核验的描述"

    def test_current_model_overrides_match_verified_capabilities(self, monkeypatch):
        from clients.new_api_client import NewAPIClient

        monkeypatch.setattr(NewAPIClient, "_model_overrides_cache", None)
        overrides = NewAPIClient._load_model_overrides()

        assert len(overrides) == 12
        assert not {
            "qwen/qwen3-coder:free",
            "qwen3-coder",
            "openai/gpt-oss-120b:free",
            "gpt-oss-120b",
            "nemotron-3-super-free",
            "opencode/nemotron-3-super-free",
        } & overrides.keys()
        assert overrides["dashscope/qwen3.6-27b"]["supports_image"] is True
        assert overrides["dashscope/qwen3.6-27b"]["context_window"] == 262144
        assert overrides["krill/gpt-5.6-luna"]["context_window"] == 1050000
        assert overrides["krill/gpt-image-2"]["enabled"] is False
        assert overrides["krill/gpt-image-2"]["supported_endpoints"] == [
            "images/generations",
            "images/edits",
            "batch",
        ]
        assert overrides[
            "openrouter/google/gemma-4-31b-it:free"
        ]["fallback_only"] is True

    def test_ordered_candidates_filters_required_capabilities_before_intel_fallback(self, monkeypatch):
        from clients import new_api_client as module
        from clients.new_api_client import NewAPIClient

        class FakeRegistry:
            def get_models_by_provider(self, provider):
                assert provider == "x"
                return [
                    {
                        "id": "smart-text",
                        "provider": "x",
                        "tier": "smart",
                        "intelligence": 9,
                        "cost_input_1m": 0.01,
                        "tags": ["general"],
                        "supports_image": False,
                        "supports_tools": False,
                        "supports_stream": True,
                        "enabled": True,
                    },
                    {
                        "id": "fast-tool",
                        "provider": "x",
                        "tier": "fast",
                        "intelligence": 4,
                        "cost_input_1m": 0.02,
                        "tags": ["tool_use"],
                        "supports_image": False,
                        "supports_tools": True,
                        "supports_stream": True,
                        "enabled": True,
                    },
                ]

            def compute_priority_score(self, model):
                from clients.model_registry import ModelRegistry
                return ModelRegistry.compute_priority_score(model)

        monkeypatch.setattr(module, "registry", FakeRegistry())
        monkeypatch.setattr(NewAPIClient, "_failure_tracker", None)
        monkeypatch.setattr(NewAPIClient, "_safe_get_failure_tracker", lambda self: None)

        client = NewAPIClient(api_key="test", base_url="http://test")
        candidates = client.get_ordered_candidates(
            "x",
            intel_floor=8,
            required_capabilities={"supports_tools": True},
        )

        assert [m["id"] for m in candidates] == ["fast-tool"]

    def test_ordered_candidates_excludes_catalog_identity_without_routing_evidence(
        self,
        monkeypatch,
    ):
        from clients import new_api_client as module
        from clients.new_api_client import NewAPIClient

        class FakeRegistry:
            def get_models_by_provider(self, provider):
                assert provider == "x"
                return [
                    {
                        "id": "name-looks-smart-vision",
                        "provider": "x",
                        "intelligence": 15,
                        "cost_input_1m": 0,
                        "routing_verified": False,
                        "routing_evidence": "catalog_identity_only",
                        "supports_image": True,
                        "capability_evidence": {
                            "supports_image": "provider_catalog"
                        },
                    },
                    {
                        "id": "curated-model",
                        "provider": "x",
                        "intelligence": 7,
                        "cost_input_1m": 0.2,
                        "routing_verified": True,
                        "routing_evidence": "curated_override",
                    },
                ]

        monkeypatch.setattr(module, "registry", FakeRegistry())
        monkeypatch.setattr(NewAPIClient, "_failure_tracker", None)
        monkeypatch.setattr(
            NewAPIClient,
            "_safe_get_failure_tracker",
            lambda self: None,
        )

        client = NewAPIClient(api_key="test", base_url="http://test")

        assert [
            item["id"]
            for item in client.get_ordered_candidates("x", intel_floor=1)
        ] == ["curated-model"]


class TestFailureTracker:
    def test_record_and_check(self):
        from clients.model_registry import ModelFailureTracker
        import time
        t = ModelFailureTracker(max_failures=2, cooldown_base_s=0.1)
        assert not t.sync_is_disabled("m1")
        # 2 failures = disabled
        for _ in range(2):
            t._failures["m1"] = t._failures.get("m1", 0) + 1
            if t._failures["m1"] >= t._max_failures:
                t._disabled_until["m1"] = time.time() + t._cooldown_base
        assert t.sync_is_disabled("m1")

    def test_cooldown_expires(self):
        from clients.model_registry import ModelFailureTracker
        import time
        t = ModelFailureTracker(max_failures=1, cooldown_base_s=0.01)
        t._failures["m1"] = 1
        t._disabled_until["m1"] = time.time() - 1  # expired
        assert not t.sync_is_disabled("m1")  # auto-cleared
        assert "m1" not in t._failures

    def test_success_resets(self):
        from clients.model_registry import ModelFailureTracker
        t = ModelFailureTracker(max_failures=3)
        t._failures["m1"] = 2
        t._disabled_until["m1"] = 9999999999
        # Simulate record_success logic
        t._failures.pop("m1", None)
        t._disabled_until.pop("m1", None)
        assert not t.sync_is_disabled("m1")
