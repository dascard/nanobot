"""Nanobot Model Preset 到 KT 原生 Provider 的唯一转换边界。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from collections.abc import Callable
from typing import Any, Protocol

from core.agent_runtime import AgentRuntimeCapabilityError, RuntimeModelRoute
from core.model_provider.preset_config import ResolvedModelPreset


class KtPresetTransport(Protocol):
    provider_id: str
    driver_type: str
    profile_id: str
    model: str
    base_url: str
    api_key: str
    timeout: float
    temperature: object
    max_tokens: int | None
    max_context: int
    reasoning_effort: str
    service_tier: str
    enable_thinking: object
    capabilities: dict[str, bool]
    extra_headers: dict[str, str]
    extra_body: dict[str, Any]
    retry_policy: dict[str, Any]
    driver_options: dict[str, Any]
    provider_name: str
    provider_native_tools: tuple[str, ...]
    codex_account_id: str


class KtModelPresetResolverAdapter:
    """在 KT 边界内调用原生 variation 解析器。"""

    def resolve(
        self,
        preset: object,
        selected_variations: dict[str, str],
    ) -> ResolvedModelPreset:
        from kohakuterrarium.llm.profile_types import LLMPreset
        from kohakuterrarium.llm.variations import (
            apply_variation_groups,
            normalize_variation_selections,
        )

        kt_preset = LLMPreset(
            name=preset.id,
            model=preset.model,
            provider=preset.provider_id,
            max_context=preset.max_context,
            max_output=preset.max_output,
            temperature=preset.temperature,
            reasoning_effort=preset.reasoning_effort,
            service_tier=preset.service_tier,
            extra_body=dict(preset.extra_body),
            retry_policy=dict(preset.retry_policy) or None,
            variation_groups=dict(preset.variation_groups),
        )
        normalized = normalize_variation_selections(
            selected_variations,
            kt_preset,
        )
        resolved_data = apply_variation_groups(
            kt_preset.to_dict(),
            kt_preset.variation_groups,
            normalized,
        )
        resolved_kt = LLMPreset.from_dict(preset.id, resolved_data)
        resolved = replace(
            preset,
            model=resolved_kt.model,
            max_context=resolved_kt.max_context,
            max_output=resolved_kt.max_output,
            temperature=resolved_kt.temperature,
            reasoning_effort=resolved_kt.reasoning_effort,
            service_tier=resolved_kt.service_tier,
            extra_body=dict(resolved_kt.extra_body),
            retry_policy=dict(resolved_kt.retry_policy or {}),
        )
        return ResolvedModelPreset(resolved, normalized)


def create_kt_provider(transport: KtPresetTransport) -> object:
    """根据解析后的 Preset 构建 KT OpenAI/Anthropic/Codex Provider。"""

    driver_type = str(transport.driver_type or "openai")
    retry_policy = dict(transport.retry_policy or {})
    max_retries = int(retry_policy.get("max_retries", 3))
    if driver_type == "codex":
        from nanobot_kt.codex_accounts import (
            AccountBoundCodexOAuthProvider,
            codex_account_pool,
        )

        account_id = str(getattr(transport, "codex_account_id", "") or "")
        if not account_id:
            account_ids = codex_account_pool.ordered_account_ids("")
            if not account_ids:
                raise RuntimeError("没有可用的 Codex OAuth 账号")
            account_id = account_ids[0]
        provider = AccountBoundCodexOAuthProvider(
            model=transport.model,
            account_id=account_id,
            reasoning_effort=transport.reasoning_effort or "medium",
            service_tier=transport.service_tier or None,
            timeout=float(transport.timeout),
            max_retries=max_retries,
            retry_policy=retry_policy or None,
        )
    elif driver_type == "anthropic":
        from kohakuterrarium.llm.anthropic_provider import AnthropicProvider

        extra_body = dict(transport.extra_body or {})
        if transport.driver_options.get("disable_prompt_caching"):
            extra_body["disable_prompt_caching"] = True
        provider = AnthropicProvider(
            api_key=transport.api_key,
            model=transport.model,
            base_url=transport.base_url or None,
            temperature=(
                float(transport.temperature)
                if transport.temperature is not None
                else None
            ),
            max_tokens=transport.max_tokens,
            timeout=float(transport.timeout),
            extra_headers=dict(transport.extra_headers or {}),
            extra_body=extra_body or None,
            max_retries=max_retries,
            service_tier=transport.service_tier or None,
            retry_policy=retry_policy or None,
            auth_as_bearer=transport.driver_options.get("auth_as_bearer"),
        )
    else:
        from kohakuterrarium.llm.openai import OpenAIProvider

        extra_body = dict(transport.extra_body or {})
        if transport.reasoning_effort:
            extra_body.setdefault("reasoning_effort", transport.reasoning_effort)
        if transport.service_tier:
            extra_body.setdefault("service_tier", transport.service_tier)
        from core.model_route_options import apply_enable_thinking_to_payload

        apply_enable_thinking_to_payload(
            extra_body,
            transport.model,
            transport.enable_thinking,
        )
        provider = OpenAIProvider(
            api_key=transport.api_key,
            model=transport.model,
            base_url=transport.base_url,
            temperature=(
                float(transport.temperature)
                if transport.temperature is not None
                else 0.7
            ),
            max_tokens=transport.max_tokens,
            timeout=float(transport.timeout),
            extra_headers=dict(transport.extra_headers or {}),
            extra_body=extra_body or None,
            max_retries=max_retries,
            echo_reasoning=bool(
                transport.driver_options.get("echo_reasoning", True)
            ),
            retry_policy=retry_policy or None,
        )

    provider.provider_name = transport.provider_name or transport.provider_id
    provider.provider_native_tools = frozenset(transport.provider_native_tools or ())
    provider._profile_max_context = int(transport.max_context)
    provider._nanobot_profile_id = transport.profile_id
    provider._nanobot_provider_id = transport.provider_id
    provider._nanobot_config_fingerprint = _transport_fingerprint(transport)
    return provider


def apply_kt_preset_model_route(
    agent: object,
    route: RuntimeModelRoute,
    transport: KtPresetTransport,
    *,
    legacy_openai_applier: Callable[..., None],
    tracer_installer: Callable[..., object] | None = None,
) -> None:
    """把 Route 原子应用到 KT Agent；无 Preset 时保留旧 OpenAI 热切换。"""

    if not route.profile_id:
        legacy_openai_applier(
            agent,
            route,
            transport,
            tracer_installer=tracer_installer,
        )
        return

    controller = getattr(agent, "controller", None)
    current = getattr(controller, "llm", None)
    if controller is None or current is None:
        raise AgentRuntimeCapabilityError("KT Agent 缺少 controller.llm")

    fingerprint = _transport_fingerprint(transport)
    if getattr(current, "_nanobot_config_fingerprint", "") == fingerprint:
        config = getattr(current, "config", None)
        if config is not None and hasattr(config, "model"):
            config.model = route.model_id
        if hasattr(current, "model"):
            current.model = route.model_id
        return

    replacement = create_kt_provider(transport)
    _carry_runtime_state(current, replacement)
    controller.llm = replacement
    if hasattr(agent, "llm"):
        agent.llm = replacement
    subagents = getattr(agent, "subagent_manager", None)
    if subagents is not None and hasattr(subagents, "llm"):
        subagents.llm = replacement
    _ensure_provider_native_tools(agent, tuple(transport.provider_native_tools or ()))

    if str(transport.driver_type) == "openai":
        if tracer_installer is None:
            from core.llm_sdk_tracing import install_openai_chat_completion_tracer

            tracer_installer = install_openai_chat_completion_tracer
        tracer_installer(
            replacement,
            provider=transport.provider_id,
            base_url=transport.base_url,
        )


def _carry_runtime_state(current: object, replacement: object) -> None:
    callbacks = getattr(current, "_emergency_drop_callbacks", None)
    if isinstance(callbacks, list):
        replacement._emergency_drop_callbacks = list(callbacks)
    prompt_cache_key = getattr(current, "prompt_cache_key", None)
    if hasattr(replacement, "prompt_cache_key"):
        replacement.prompt_cache_key = prompt_cache_key


def _ensure_provider_native_tools(agent: object, tool_names: tuple[str, ...]) -> None:
    if not tool_names:
        return
    registry = getattr(agent, "registry", None)
    if registry is None:
        return
    from kohakuterrarium.builtins.tool_catalog import get_builtin_tool

    existing = set(registry.list_tools())
    for name in tool_names:
        if name in existing:
            continue
        tool = get_builtin_tool(name)
        if tool is not None:
            registry.register_tool(tool)
            existing.add(name)


def _transport_fingerprint(transport: KtPresetTransport) -> str:
    payload = {
        "provider_id": transport.provider_id,
        "driver_type": transport.driver_type,
        "profile_id": transport.profile_id,
        "model": transport.model,
        "base_url": transport.base_url,
        "api_key_sha256": hashlib.sha256(
            str(transport.api_key or "").encode("utf-8")
        ).hexdigest(),
        "timeout": transport.timeout,
        "temperature": transport.temperature,
        "max_tokens": transport.max_tokens,
        "max_context": transport.max_context,
        "reasoning_effort": transport.reasoning_effort,
        "service_tier": transport.service_tier,
        "enable_thinking": transport.enable_thinking,
        "capabilities": transport.capabilities,
        "extra_headers": transport.extra_headers,
        "extra_body": transport.extra_body,
        "retry_policy": transport.retry_policy,
        "driver_options": transport.driver_options,
        "provider_name": transport.provider_name,
        "provider_native_tools": transport.provider_native_tools,
        "codex_account_id": str(
            getattr(transport, "codex_account_id", "") or ""
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "KtModelPresetResolverAdapter",
    "KtPresetTransport",
    "apply_kt_preset_model_route",
    "create_kt_provider",
]
