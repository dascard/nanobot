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
