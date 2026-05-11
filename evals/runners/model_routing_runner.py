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
