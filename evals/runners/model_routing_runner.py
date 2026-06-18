"""Model routing suite runner——select_model、disabled、settings override。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def _required_capabilities_from_input(inp: dict) -> dict[str, bool]:
    required: dict[str, bool] = {}
    if inp.get("has_image"):
        required["supports_image"] = True
    if inp.get("has_tools"):
        required["supports_tools"] = True
    if inp.get("stream"):
        required["supports_stream"] = True

    explicit = inp.get("required_capabilities") or {}
    if isinstance(explicit, dict):
        for key, value in explicit.items():
            if value:
                required[str(key)] = True
    return required


def run_model_routing_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    models = inp.get("models", [])
    provider = str(inp.get("provider") or "new-api")
    intel_floor = int(inp.get("intel_floor", inp.get("min_intelligence", 0)) or 0)

    if models:
        from clients.model_registry import ModelRegistry
        from clients import new_api_client
        from clients.new_api_client import NewAPIClient

        reg = ModelRegistry.__new__(ModelRegistry)
        reg.data = {"models": list(models), "last_updated": "2026-01-01T00:00:00"}
        required_capabilities = _required_capabilities_from_input(inp)

        orig_registry = new_api_client.registry
        orig_safe_tracker = NewAPIClient._safe_get_failure_tracker
        new_api_client.registry = reg
        NewAPIClient._safe_get_failure_tracker = lambda self: None
        try:
            client = NewAPIClient(
                api_key="eval-key",
                base_url="http://eval.invalid/v1",
                registry_provider=provider,
            )
            candidates = client.get_ordered_candidates(
                provider=provider,
                intel_floor=intel_floor,
                max_cost=inp.get("max_cost"),
                required_capabilities=required_capabilities,
            )
        finally:
            new_api_client.registry = orig_registry
            NewAPIClient._safe_get_failure_tracker = orig_safe_tracker

        result = candidates[0].get("id", "") if candidates else ""
        out.model_used = result
        out.raw["auto_routing_called"] = bool(result)
        out.raw["required_capabilities"] = required_capabilities
        out.raw["ordered_candidates"] = [c.get("id", "") for c in candidates]

    requested_tier = inp.get("requested_tier", "fast")
    if models and requested_tier and not out.model_used and not out.raw.get("required_capabilities"):
        from clients.model_registry import ModelRegistry
        reg = ModelRegistry.__new__(ModelRegistry)
        reg.data = {"models": list(models), "last_updated": "2026-01-01T00:00:00"}
        result = reg.select_model(provider, tier=requested_tier)
        out.model_used = result or ""
        out.raw["auto_routing_called"] = result is not None

    # settings override test: 调用真实 settings 服务
    settings_model_reply = inp.get("settings_model_reply", "")
    if settings_model_reply:
        from core.settings_service import settings
        import os

        # monkeypatch: 让 settings.get("model.reply") 返回测试值
        orig_get = settings.get

        def monkey_get(key, default=None):
            if key == "model.reply":
                return settings_model_reply
            return orig_get(key, default)

        settings.get = monkey_get
        try:
            # 模拟 bridge 中的 manual_reply_model 解析链（bridge.py:645-651）
            from core.settings_service import settings as svc
            manual_reply_model = str(
                svc.get("model.reply")
                or os.environ.get("LLM_MODEL_REPLY", "")
                or ""
            ).strip()
            out.model_used = manual_reply_model
            out.raw["auto_routing_called"] = False
        finally:
            settings.get = orig_get

    # env_model fallback: 不通过 settings
    env_model = inp.get("env_model", "")
    if env_model and not settings_model_reply:
        import os
        manual = env_model or ""
        out.model_used = manual
        out.raw["auto_routing_called"] = False

    return out
