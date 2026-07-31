"""Nanobot 模型路由到 KT Provider/Preset 的运行时适配。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger("nanobot.kt.model_runtime")


@dataclass(frozen=True)
class ReplyRoutePlan:
    provider_id: str
    registry_provider: str
    base_url: str
    api_key: str
    timeout: float
    driver_type: str = "openai"
    profile_id: str = ""
    model: str = ""
    temperature: object = None
    max_tokens: int | None = None
    max_context: int = 128000
    reasoning_effort: str = ""
    service_tier: str = ""
    enable_thinking: object = "auto"
    capabilities: dict[str, bool] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(default_factory=dict)
    driver_options: dict[str, Any] = field(default_factory=dict)
    provider_name: str = ""
    provider_native_tools: tuple[str, ...] = ()


def registry_provider_for_route(provider_id: str) -> str:
    provider_id = (provider_id or "").strip()
    if not provider_id:
        return "new-api"
    from clients.classifier_client import _get_provider_config

    cfg = _get_provider_config(provider_id)
    if cfg and cfg.get("registry_provider"):
        return cfg["registry_provider"]
    if provider_id in ("newapi", "new-api"):
        return "new-api"
    return provider_id


def resolve_reply_route_plans(
    *,
    default_base_url: str,
    default_api_key: str,
) -> list[ReplyRoutePlan]:
    """优先解析 Route Binding；未绑定时返回旧 Route 的兼容计划。"""

    from core.model_provider.preset_config import resolve_route_binding_candidates

    bound_candidates = resolve_route_binding_candidates("reply")
    if not bound_candidates:
        return [_resolve_legacy_reply_route_plan(
            default_base_url=default_base_url,
            default_api_key=default_api_key,
        )]

    from core.model_provider.provider_config import get_provider_instance

    plans: list[ReplyRoutePlan] = []
    unavailable: list[str] = []
    for candidate, resolved in bound_candidates:
        preset = resolved.preset
        provider = get_provider_instance(preset.provider_id)
        if provider is None:
            unavailable.append(f"{candidate.preset_id}: Provider 不存在")
            continue
        if not preset.enabled:
            unavailable.append(f"{candidate.preset_id}: Preset 已禁用")
            continue
        if not provider.enabled:
            unavailable.append(f"{candidate.preset_id}: Provider 已禁用")
            continue
        if not provider.agent_runtime_supported:
            unavailable.append(
                f"{candidate.preset_id}: KT Driver {provider.driver_type} 未接入"
            )
            continue
        if not provider.runtime_available:
            unavailable.append(
                f"{candidate.preset_id}: {provider.runtime_unavailable_reason}"
            )
            continue
        if not provider.credential_configured:
            unavailable.append(f"{candidate.preset_id}: 凭据未配置")
            continue
        if provider.driver_type != "codex" and not provider.base_url:
            unavailable.append(f"{candidate.preset_id}: Base URL 未配置")
            continue
        plans.append(ReplyRoutePlan(
            provider_id=provider.id,
            registry_provider=provider.registry_provider or provider.id,
            base_url=provider.base_url,
            api_key=provider.api_key,
            timeout=preset.timeout,
            driver_type=provider.driver_type,
            profile_id=preset.id,
            model=preset.model,
            temperature=preset.temperature,
            max_tokens=preset.max_output,
            max_context=preset.max_context,
            reasoning_effort=preset.reasoning_effort,
            service_tier=preset.service_tier,
            enable_thinking=preset.enable_thinking,
            capabilities=dict(preset.capabilities),
            extra_headers=dict(preset.extra_headers),
            extra_body=dict(preset.extra_body),
            retry_policy=dict(preset.retry_policy),
            driver_options=dict(preset.driver_options),
            provider_name=provider.provider_name,
            provider_native_tools=tuple(provider.provider_native_tools),
        ))
    if not plans:
        detail = "；".join(unavailable) or "没有可用候选"
        raise RuntimeError(f"reply Route Binding 无可用 Preset：{detail}")
    if unavailable:
        logger.warning("reply Route 跳过不可用 Preset：%s", "；".join(unavailable))
    return plans


def resolve_reply_route_plan(
    *,
    default_base_url: str,
    default_api_key: str,
) -> ReplyRoutePlan:
    """兼容旧调用方，返回候选链的首个可用计划。"""

    return resolve_reply_route_plans(
        default_base_url=default_base_url,
        default_api_key=default_api_key,
    )[0]


def _resolve_legacy_reply_route_plan(
    *,
    default_base_url: str,
    default_api_key: str,
) -> ReplyRoutePlan:
    from clients.classifier_client import (
        ensure_model_route_enabled,
        resolve_model_route,
    )

    route = resolve_model_route("reply")
    ensure_model_route_enabled("reply", route)
    provider_id = str(route.get("provider_id", "") or "")
    max_tokens_raw = route.get("max_tokens")
    max_tokens = int(max_tokens_raw) if max_tokens_raw else None
    if max_tokens is not None and max_tokens <= 0:
        max_tokens = None
    return ReplyRoutePlan(
        provider_id=provider_id,
        registry_provider=registry_provider_for_route(provider_id),
        base_url=str(route.get("base_url", "") or "").rstrip("/")
        or default_base_url,
        api_key=str(route.get("api_key", "") or "") or default_api_key,
        timeout=float(route.get("timeout") or 120.0),
        driver_type=str(route.get("driver_type") or "openai"),
        model=str(route.get("model") or ""),
        temperature=route.get("temperature"),
        max_tokens=max_tokens,
        max_context=int(route.get("max_context") or 128000),
        enable_thinking=route.get("enable_thinking", "auto"),
        capabilities={
            "supports_stream": True,
            "supports_tools": True,
            "supports_image": True,
        },
        provider_name=provider_id,
    )


class PresetRouteClient:
    """让既有候选/熔断循环消费显式 Preset fallback 链。"""

    def __init__(self, plans: list[ReplyRoutePlan]) -> None:
        self.plans = list(plans)

    async def sync_models_to_registry(self, force: bool = False) -> dict[str, Any]:
        return {"ok": True, "source": "model_preset", "count": len(self.plans)}

    @staticmethod
    def estimate_complexity(
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> int:
        text = "".join(str(item.get("content") or "") for item in messages)
        return max(1, min(10, 2 + len(text) // 500 + (2 if tools else 0)))

    def get_ordered_candidates(
        self,
        provider: str,
        intel_floor: int,
        exclude_models: list[str] | None = None,
        max_cost: float | None = None,
        avoid_tags: list[str] | None = None,
        required_capabilities: dict[str, bool] | None = None,
    ) -> list[dict[str, Any]]:
        del provider, intel_floor, max_cost, avoid_tags
        excluded = set(exclude_models or ())
        tracker = None
        try:
            from clients.new_api_client import NewAPIClient

            tracker = NewAPIClient.get_failure_tracker()
        except Exception:
            pass
        candidates: list[dict[str, Any]] = []
        for index, plan in enumerate(self.plans):
            if plan.model in excluded:
                continue
            if tracker is not None and tracker.sync_is_disabled(plan.model):
                continue
            capabilities = dict(plan.capabilities or {})
            if any(
                required
                and capabilities.get(name) is not True
                for name, required in (required_capabilities or {}).items()
            ):
                continue
            candidates.append({
                "id": plan.model,
                "provider": plan.registry_provider,
                "intelligence": 15 - index,
                "cost_input_1m": 0,
                "cost_output_1m": 0,
                "context_window": plan.max_context,
                **capabilities,
                "_route_plan": plan,
                "_preset_id": plan.profile_id,
            })
        return candidates


__all__ = [
    "PresetRouteClient",
    "ReplyRoutePlan",
    "registry_provider_for_route",
    "resolve_reply_route_plan",
    "resolve_reply_route_plans",
]
