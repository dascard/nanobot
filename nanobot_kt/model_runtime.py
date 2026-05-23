"""Nanobot 模型路由运行时适配。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReplyRoutePlan:
    provider_id: str
    registry_provider: str
    base_url: str
    api_key: str
    timeout: float
    temperature: object = None
    max_tokens: int | None = None
    enable_thinking: object = "auto"


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


def resolve_reply_route_plan(*, default_base_url: str, default_api_key: str) -> ReplyRoutePlan:
    from clients.classifier_client import ensure_model_route_enabled, resolve_model_route

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
        base_url=str(route.get("base_url", "") or "").rstrip("/") or default_base_url,
        api_key=str(route.get("api_key", "") or "") or default_api_key,
        timeout=float(route.get("timeout") or 120.0),
        temperature=route.get("temperature"),
        max_tokens=max_tokens,
        enable_thinking=route.get("enable_thinking", "auto"),
    )
