"""模型注册表单元测试——select_model 路由 + enabled 过滤。"""
from clients.model_registry import ModelRegistry


def _make_registry(models: list[dict]) -> ModelRegistry:
    r = ModelRegistry.__new__(ModelRegistry)
    r.data = {"models": models, "last_updated": "2026-01-01T00:00:00"}
    return r


class TestSelectModel:
    def test_selects_highest_score_in_tier(self):
        r = _make_registry([
            {"id": "cheap", "provider": "x", "tier": "fast",
             "intelligence": 5, "cost_input_1m": 0.5, "enabled": True},
            {"id": "better", "provider": "x", "tier": "fast",
             "intelligence": 7, "cost_input_1m": 0.5, "enabled": True},
        ])
        assert r.select_model("x", tier="fast") == "better"

    def test_skips_disabled_model(self):
        r = _make_registry([
            {"id": "disabled", "provider": "x", "tier": "fast",
             "intelligence": 10, "cost_input_1m": 0.1, "enabled": False},
            {"id": "ok", "provider": "x", "tier": "fast",
             "intelligence": 5, "cost_input_1m": 1.0, "enabled": True},
        ])
        assert r.select_model("x", tier="fast") == "ok"

    def test_skips_disabled_even_when_better(self):
        r = _make_registry([
            {"id": "disabled-great", "provider": "x", "tier": "smart",
             "intelligence": 15, "cost_input_1m": 0.0, "enabled": False},
            {"id": "ok-mediocre", "provider": "x", "tier": "smart",
             "intelligence": 3, "cost_input_1m": 5.0, "enabled": True},
        ])
        assert r.select_model("x", tier="smart") == "ok-mediocre"

    def test_skips_disabled_in_cross_tier_free_prefer(self):
        """跨层免费优先不应选中 disabled 免费模型"""
        r = _make_registry([
            {"id": "paid-smart", "provider": "x", "tier": "smart",
             "intelligence": 8, "cost_input_1m": 2.0, "enabled": True},
            {"id": "free-disabled", "provider": "x", "tier": "fast",
             "intelligence": 8, "cost_input_1m": 0.0, "enabled": False,
             "tags": ["free"]},
            {"id": "free-ok", "provider": "x", "tier": "fast",
             "intelligence": 7, "cost_input_1m": 0.0, "enabled": True,
             "tags": ["free"]},
        ])
        selected = r.select_model("x", tier="smart", prefer_free=True)
        assert selected == "free-ok", f"Expected free-ok, got {selected}"

    def test_disabled_defaults_to_true(self):
        r = _make_registry([
            {"id": "no-field", "provider": "x", "tier": "fast",
             "intelligence": 5, "cost_input_1m": 1.0},
        ])
        assert r.select_model("x", tier="fast") == "no-field"

    def test_all_disabled_falls_back_to_cheapest(self):
        r = _make_registry([
            {"id": "d1", "provider": "x", "tier": "smart",
             "intelligence": 10, "cost_input_1m": 0.5, "enabled": False},
            {"id": "d2", "provider": "x", "tier": "smart",
             "intelligence": 8, "cost_input_1m": 1.0, "enabled": False},
        ])
        # smart 全 disabled → 降级 fast（空）→ fallback 返回最便宜
        result = r.select_model("x", tier="smart")
        assert result in ("d1", "d2")

    def test_select_model_treats_none_cost_as_unknown_under_budget(self):
        r = _make_registry([
            {"id": "unknown-cost", "provider": "x", "tier": "fast",
             "intelligence": 10, "cost_input_1m": None, "enabled": True},
            {"id": "ok", "provider": "x", "tier": "fast",
             "intelligence": 6, "cost_input_1m": 0.2, "enabled": True},
        ])
        assert r.select_model("x", tier="fast", max_cost=1.0) == "ok"

    def test_add_or_update_many_normalizes_none_cost(self, monkeypatch):
        r = _make_registry([])
        monkeypatch.setattr(r, "save_registry", lambda: None)

        assert r.add_or_update_many([
            {"id": "m", "provider": "x", "tier": "fast",
             "intelligence": 5, "cost_input_1m": None, "cost_output_1m": None}
        ]) == 1

        saved = r.data["models"][0]
        assert saved["cost_input_1m"] == 999.0
        assert saved["cost_output_1m"] == 999.0

    def test_add_or_update_many_does_not_guess_capabilities_from_model_name(
        self,
        monkeypatch,
    ):
        r = _make_registry([])
        monkeypatch.setattr(r, "save_registry", lambda: None)

        assert r.add_or_update_many([
            {
                "id": "qwen/qwen-vl-plus",
                "provider": "x",
                "tier": "smart",
                "intelligence": 7,
                "cost_input_1m": None,
                "cost_output_1m": None,
                "tags": ["vision", "multimodal"],
            },
            {
                "id": "legacy/text-only",
                "provider": "x",
                "tier": "fast",
                "intelligence": 5,
                "cost_input_1m": 0.1,
                "cost_output_1m": 0.2,
                "tags": ["general"],
            },
        ]) == 2

        vision = r.get_model_info("qwen/qwen-vl-plus")
        text = r.get_model_info("legacy/text-only")
        assert vision["supports_image"] is False
        assert vision["supports_tools"] is False
        assert vision["supports_stream"] is False
        assert text["supports_image"] is False
        assert text["supports_tools"] is False
        assert text["supports_stream"] is False
        assert set(vision["capability_evidence"].values()) == {"unknown"}

    def test_explicit_capability_fields_create_verified_evidence(
        self,
        monkeypatch,
    ):
        r = _make_registry([])
        monkeypatch.setattr(r, "save_registry", lambda: None)

        r.add_or_update_model({
            "id": "explicit-model",
            "provider": "x",
            "supports_image": True,
            "supports_tools": False,
            "supports_stream": True,
        })

        saved = r.get_model_info("explicit-model")
        assert saved["capability_evidence"] == {
            "supports_image": "explicit_descriptor",
            "supports_tools": "explicit_descriptor",
            "supports_stream": "explicit_descriptor",
        }

    def test_replace_provider_models_replaces_snapshot_and_keeps_other_provider(
        self,
        monkeypatch,
    ):
        common = {
            "tier": "fast",
            "cost_input_1m": 0.1,
            "cost_output_1m": 0.2,
            "supports_image": False,
            "supports_tools": True,
            "supports_stream": True,
            "tags": ["general"],
        }
        r = _make_registry([
            {
                **common,
                "id": "stale",
                "provider": "new-api",
                "intelligence": 5,
            },
            {
                **common,
                "id": "kept",
                "provider": "new-api",
                "intelligence": 6,
            },
            {
                **common,
                "id": "other",
                "provider": "local",
                "intelligence": 7,
            },
        ])
        monkeypatch.setattr(r, "save_registry", lambda: None)

        changed = r.replace_provider_models("new-api", [
            {
                **common,
                "id": "kept",
                "provider": "untrusted-value",
                "intelligence": 8,
            },
            {
                **common,
                "id": "new",
                "provider": "untrusted-value",
                "intelligence": 9,
            },
        ])

        assert changed == 3
        assert [m["id"] for m in r.get_models_by_provider("new-api")] == [
            "kept",
            "new",
        ]
        assert [m["id"] for m in r.get_models_by_provider("local")] == ["other"]
        assert r.get_model_info("stale") is None
