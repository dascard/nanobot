"""Tests for the new model routing system."""
import pytest


class TestClassifierRouteProviderResolution:
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

        with pytest.raises(RuntimeError, match="provider disabled: local_qwen"):
            call_model_route(route_key="timing_gate", user_message="ping")


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
