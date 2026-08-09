"""Nanobot 模型路由到 KT Provider/Preset 的运行时适配。"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from core.model_provider.route_plan import ReplyRoutePlan

logger = logging.getLogger("nanobot.kt.model_runtime")


def registry_provider_for_route(provider_id: str) -> str:
    from clients.classifier_client import registry_provider_for_route as resolve

    return resolve(provider_id)


def resolve_reply_route_plans(
    *,
    default_base_url: str,
    default_api_key: str,
    session_id: str = "",
    preferred_profile_id: str = "",
) -> list[ReplyRoutePlan]:
    """优先解析 Route Binding；未绑定时返回旧 Route 的兼容计划。"""

    from core.model_provider.preset_config import resolve_route_binding_candidates

    bound_candidates = resolve_route_binding_candidates("reply")
    if not bound_candidates:
        plans = [_resolve_legacy_reply_route_plan(
            default_base_url=default_base_url,
            default_api_key=default_api_key,
        )]
        plans = _apply_evolution_reply_routing(plans, session_id)
        return _prioritize_reply_profile(plans, preferred_profile_id)

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
        provider_descriptor = getattr(provider, "descriptor", None)
        if provider_descriptor is None:
            from core.model_provider.provider_config import (
                provider_descriptor_for_driver,
            )

            provider_descriptor = provider_descriptor_for_driver(
                provider.driver_type,
                provider_id=provider.id,
                display_name=str(
                    getattr(provider, "display_name", "") or provider.id
                ),
            )
        plan = ReplyRoutePlan(
            provider_id=provider.id,
            registry_provider=provider.registry_provider or provider.id,
            base_url=provider.base_url,
            api_key=provider.api_key,
            timeout=preset.timeout,
            driver_type=provider.driver_type,
            request_protocol=provider_descriptor.request_protocol.value,
            request_path=provider_descriptor.request_path,
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
            capability_evidence={
                key: "operator_model_config"
                for key in preset.capabilities
            },
            routing_evidence="operator_model_config",
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
    plans = _apply_evolution_reply_routing(plans, session_id)
    return _prioritize_reply_profile(plans, preferred_profile_id)


def _apply_evolution_reply_routing(
    plans: list[ReplyRoutePlan],
    session_id: str,
) -> list[ReplyRoutePlan]:
    """只重排已解析并验证的 Profile，不允许候选注入新 Provider。"""

    from core.evolution_control.runtime import reorder_routing_candidates

    ordered, evidence = reorder_routing_candidates(
        plans,
        route_key="reply",
        subject_id=session_id,
        candidate_id=lambda item: str(item.profile_id or item.model or ""),
    )
    if evidence.get("applied") is not True:
        return ordered
    release_id = str(evidence.get("release_id") or "")
    return [
        replace(
            plan,
            routing_evidence=f"evolution_canary:{release_id}",
        )
        for plan in ordered
    ]


def resolve_gateway_reply_route_plans(
    *,
    default_base_url: str,
    default_api_key: str,
    session_id: str,
    gateway_binding_id: str,
) -> list[ReplyRoutePlan]:
    """按 Gateway 当前生效 Profile 解析已验证的 reply 候选。"""

    from core.gateway_control import active_gateway_model_profile

    return resolve_reply_route_plans(
        default_base_url=default_base_url,
        default_api_key=default_api_key,
        session_id=session_id,
        preferred_profile_id=active_gateway_model_profile(
            gateway_binding_id
        ),
    )


def _prioritize_reply_profile(
    plans: list[ReplyRoutePlan],
    preferred_profile_id: str,
) -> list[ReplyRoutePlan]:
    """只在已验证 reply 候选内提升会话指定 Profile。"""

    preferred = str(preferred_profile_id or "").strip()
    if not preferred:
        return list(plans)
    selected = [plan for plan in plans if plan.profile_id == preferred]
    if not selected:
        raise RuntimeError(
            "会话指定的模型 Profile 已不在当前 reply Route 候选中"
        )
    return selected + [plan for plan in plans if plan.profile_id != preferred]


def reply_model_profile_descriptors(
    plans: list[ReplyRoutePlan],
) -> list[dict[str, object]]:
    """返回不含凭据、URL 和账号 ID 的可选模型 Profile。"""

    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for plan in plans:
        profile_id = str(plan.profile_id or "").strip()
        if not profile_id or profile_id in seen:
            continue
        seen.add(profile_id)
        result.append({
            "profile_id": profile_id,
            "model": str(plan.model or ""),
            "provider_id": str(plan.provider_id or ""),
            "provider_name": str(plan.provider_name or ""),
            "supports_tools": bool(
                dict(plan.capabilities or {}).get("supports_tools", False)
            ),
            "supports_image": bool(
                dict(plan.capabilities or {}).get("supports_image", False)
            ),
        })
    return result


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
    from clients.model_registry import normalize_model_capability_fields, registry
    from core.model_provider.provider_config import provider_descriptor_for_driver

    model_id = str(route.get("model") or "")
    model_info = registry.get_model_info(model_id) if model_id else None
    cost_input_1m = route.get("cost_input_1m")
    cost_output_1m = route.get("cost_output_1m")
    if model_info is not None:
        normalized_model = normalize_model_capability_fields(model_info)
        cost_input_1m = normalized_model.get("cost_input_1m")
        cost_output_1m = normalized_model.get("cost_output_1m")
        capabilities = {
            key: bool(normalized_model.get(key))
            for key in (
                "supports_stream",
                "supports_tools",
                "supports_image",
            )
        }
        capability_evidence = dict(
            normalized_model.get("capability_evidence") or {}
        )
        routing_evidence = str(
            normalized_model.get("routing_evidence")
            or "explicit_model_descriptor"
        )
    else:
        capabilities = {
            "supports_stream": True,
            "supports_tools": True,
            "supports_image": False,
        }
        capability_evidence = {
            key: "legacy_operator_route"
            for key in capabilities
        }
        routing_evidence = "legacy_operator_route"
    driver_type = str(route.get("driver_type") or "openai")
    descriptor = provider_descriptor_for_driver(
        driver_type,
        provider_id=provider_id or "legacy_provider",
        display_name=provider_id or "Legacy Provider",
    )
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
        request_protocol=descriptor.request_protocol.value,
        request_path=descriptor.request_path,
        model=model_id,
        temperature=route.get("temperature"),
        max_tokens=max_tokens,
        max_context=int(route.get("max_context") or 128000),
        cost_input_1m=cost_input_1m,
        cost_output_1m=cost_output_1m,
        enable_thinking=route.get("enable_thinking", "auto"),
        capabilities=capabilities,
        capability_evidence=capability_evidence,
        routing_evidence=routing_evidence,
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
                "capability_evidence": dict(plan.capability_evidence),
                "routing_evidence": plan.routing_evidence,
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
    "reply_model_profile_descriptors",
    "resolve_gateway_reply_route_plans",
    "resolve_reply_route_plan",
    "resolve_reply_route_plans",
]
