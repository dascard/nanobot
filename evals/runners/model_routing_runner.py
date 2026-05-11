"""Model routing suite runner——select_model、disabled、settings override。"""
from __future__ import annotations

from evals.schema import EvalCase, EvalOutput


def run_model_routing_case(case: EvalCase) -> EvalOutput:
    inp = case.input
    out = EvalOutput(case_id=case.id, suite=case.suite, raw=dict(inp))

    models = inp.get("models", [])
    requested_tier = inp.get("requested_tier", "fast")

    if models and requested_tier:
        from clients.model_registry import ModelRegistry
        reg = ModelRegistry.__new__(ModelRegistry)
        reg.data = {"models": list(models), "last_updated": "2026-01-01T00:00:00"}
        result = reg.select_model("new-api", tier=requested_tier)
        out.model_used = result or ""
        out.raw["auto_routing_called"] = result is not None

    # settings override test
    env_model = inp.get("env_model", "")
    settings_model_reply = inp.get("settings_model_reply", "")
    if env_model or settings_model_reply:
        # 模拟 bridge 中的 manual_reply_model 解析链
        manual = settings_model_reply or env_model or ""
        out.model_used = manual
        out.raw["auto_routing_called"] = False

    return out
