"""Nanobot 模型路由到 KT Provider/Preset 的运行时适配。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
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
    cost_input_1m: float | None = None
    cost_output_1m: float | None = None
    intelligence: int = 0
    fallback_only: bool = False
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
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
    codex_account_id: str = ""


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
    session_id: str = "",
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
        identity = (
            str(getattr(candidate, "identity", "") or "")
            or str(getattr(candidate, "preset_id", "") or "")
            or preset.id
        )
        if provider is None:
            unavailable.append(f"{identity}: Provider 不存在")
            continue
        if not preset.enabled:
            unavailable.append(f"{identity}: 模型已禁用")
            continue
        if not provider.enabled:
            unavailable.append(f"{identity}: Provider 已禁用")
            continue
        if not provider.agent_runtime_supported:
            unavailable.append(
                f"{identity}: KT Driver {provider.driver_type} 未接入"
            )
            continue
        if not provider.runtime_available:
            unavailable.append(
                f"{identity}: {provider.runtime_unavailable_reason}"
            )
            continue
        if not provider.credential_configured:
            unavailable.append(f"{identity}: 凭据未配置")
            continue
        if provider.driver_type != "codex" and not provider.base_url:
            unavailable.append(f"{identity}: Base URL 未配置")
            continue
        plan = ReplyRoutePlan(
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
            cost_input_1m=getattr(preset, "cost_input_1m", None),
            cost_output_1m=getattr(preset, "cost_output_1m", None),
            intelligence=preset.intelligence,
            fallback_only=preset.fallback_only,
            input_modalities=tuple(preset.input_modalities),
            output_modalities=tuple(preset.output_modalities),
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
        )
        if provider.driver_type == "codex":
            from nanobot_kt.codex_accounts import codex_account_pool

            account_ids = codex_account_pool.ordered_account_ids(session_id)
            if not account_ids:
                unavailable.append(f"{identity}: 没有可用的 Codex OAuth 账号")
                continue
            plans.extend(
                replace(plan, codex_account_id=account_id)
                for account_id in account_ids
            )
        else:
            plans.append(plan)
    if not plans:
        detail = "；".join(unavailable) or "没有可用候选"
        raise RuntimeError(f"reply Route Binding 无可用模型：{detail}")
    if unavailable:
        logger.warning("reply Route 跳过不可用模型：%s", "；".join(unavailable))
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
        del provider
        excluded = set(exclude_models or ())
        avoided = {str(tag).strip().lower() for tag in (avoid_tags or ())}
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
            if plan.cost_input_1m == 0 and plan.cost_output_1m == 0:
                pricing_tags = ["free"]
            elif (
                plan.cost_input_1m is not None
                or plan.cost_output_1m is not None
            ):
                pricing_tags = ["paid"]
            else:
                pricing_tags = ["price_unknown"]
            if avoided.intersection(pricing_tags):
                continue
            if (
                max_cost is not None
                and plan.cost_input_1m is not None
                and plan.cost_input_1m > max_cost
            ):
                continue
            health_key = plan.model
            if plan.codex_account_id:
                from nanobot_kt.codex_accounts import codex_account_health_key

                health_key = codex_account_health_key(
                    plan.model,
                    plan.codex_account_id,
                )
            if tracker is not None and tracker.sync_is_disabled(health_key):
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
                "intelligence": plan.intelligence,
                "cost_input_1m": plan.cost_input_1m,
                "cost_output_1m": plan.cost_output_1m,
                "tags": pricing_tags,
                "context_window": plan.max_context,
                "fallback_only": plan.fallback_only,
                "input_modalities": list(plan.input_modalities),
                "output_modalities": list(plan.output_modalities),
                **capabilities,
                "_route_plan": plan,
                "_preset_id": plan.profile_id,
                "_configured_index": index,
                "_candidate_key": health_key,
                "_health_key": health_key,
                "_codex_account_id": plan.codex_account_id,
            })
        def sort_key(item: dict[str, Any]) -> tuple[int, int, float, int, int, int]:
            is_free = (
                item.get("cost_input_1m") == 0
                and item.get("cost_output_1m") == 0
            )
            intelligence = int(item.get("intelligence") or 0)
            below_floor = intelligence < intel_floor
            if item.get("fallback_only") or (is_free and below_floor):
                quality_bucket = 2
            elif below_floor:
                quality_bucket = 1
            else:
                quality_bucket = 0
            price_unknown = int(
                item.get("cost_input_1m") is None
                or item.get("cost_output_1m") is None
            )
            total_price = (
                float(item.get("cost_input_1m") or 0)
                + float(item.get("cost_output_1m") or 0)
                if not price_unknown
                else float("inf")
            )
            modality_count = max(
                0, len(item.get("input_modalities") or ["text"]) - 1
            ) + max(0, len(item.get("output_modalities") or ["text"]) - 1)
            return (
                quality_bucket,
                price_unknown,
                total_price,
                modality_count,
                -intelligence,
                int(item.get("_configured_index") or 0),
            )

        candidates.sort(key=sort_key)
        return candidates


__all__ = [
    "PresetRouteClient",
    "ReplyRoutePlan",
    "registry_provider_for_route",
    "resolve_reply_route_plan",
    "resolve_reply_route_plans",
]
