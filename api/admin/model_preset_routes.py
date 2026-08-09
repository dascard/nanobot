"""Model Preset、Route Binding 与 KT/Codex 管理 API。"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.admin.common import audit, client_ip, verify_admin
from core.db import get_db, system_setting_repository
from core.settings_admin_service import (
    SystemSettingCommandService,
)


router = APIRouter(tags=["admin-model-presets"])


class RetryPolicyBody(BaseModel):
    max_retries: int = Field(default=3, ge=0, le=10)
    base_delay: float = Field(default=1.0, ge=0, le=60)
    max_delay: float = Field(default=30.0, ge=0, le=300)
    jitter: float = Field(default=0.25, ge=0, le=1)
    retry_classes: list[
        Literal["rate_limit", "server", "transient", "overflow"]
    ] = Field(default_factory=lambda: ["rate_limit", "server", "transient"])


class ModelPresetWriteBody(BaseModel):
    display_name: str = Field(default="", max_length=100)
    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    enabled: bool = True
    max_context: int = Field(default=128000, ge=1024, le=4_000_000)
    max_output: int = Field(default=16384, ge=1, le=1_000_000)
    temperature: float | None = Field(default=1.0, ge=0, le=2)
    reasoning_effort: str = Field(default="", max_length=32)
    service_tier: str = Field(default="", max_length=64)
    cost_input_1m: float | None = Field(default=None, ge=0)
    cost_output_1m: float | None = Field(default=None, ge=0)
    intelligence: int = Field(default=0, ge=0, le=15)
    fallback_only: bool = False
    timeout: float = Field(default=120.0, ge=1, le=900)
    enable_thinking: Literal["auto", "true", "false"] = "auto"
    capabilities: dict[str, bool] = Field(default_factory=lambda: {
        "supports_stream": True,
        "supports_tools": True,
        "supports_image": False,
    })
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    retry_policy: RetryPolicyBody = Field(default_factory=RetryPolicyBody)
    variation_groups: dict[str, dict[str, dict[str, Any]]] = Field(
        default_factory=dict
    )
    driver_options: dict[str, Any] = Field(default_factory=dict)
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    supported_endpoints: list[str] = Field(
        default_factory=lambda: ["chat/completions"]
    )


class ModelPresetCreateBody(ModelPresetWriteBody):
    id: str = Field(min_length=1, max_length=64)


class PresetResolveBody(BaseModel):
    selected_variations: dict[str, str] = Field(default_factory=dict)


class PresetTestBody(PresetResolveBody):
    prompt: str = Field(
        default="请只回复：Nanobot Preset 连接正常",
        min_length=1,
        max_length=2000,
    )


class ModelDefaultTestBody(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    prompt: str = Field(
        default="请只回复：Nanobot 模型连接正常",
        min_length=1,
        max_length=2000,
    )


class RouteModelOverridesBody(BaseModel):
    max_output: int | None = Field(default=None, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    service_tier: str | None = Field(default=None, max_length=64)
    timeout: float | None = Field(default=None, ge=1, le=900)
    enable_thinking: Literal["auto", "true", "false"] | None = None
    extra_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None
    retry_policy: RetryPolicyBody | None = None
    driver_options: dict[str, Any] | None = None


class RouteBindingCandidateBody(BaseModel):
    provider_id: str = Field(default="", max_length=64)
    model: str = Field(default="", max_length=256)
    overrides: RouteModelOverridesBody = Field(
        default_factory=RouteModelOverridesBody
    )
    # 旧字段保留到滚动部署完成。
    preset_id: str = Field(default="", max_length=64)
    selected_variations: dict[str, str] = Field(default_factory=dict)


class RouteBindingBody(BaseModel):
    candidates: list[RouteBindingCandidateBody] = Field(
        min_length=1,
        max_length=8,
    )
    min_intelligence: int = Field(default=0, ge=0, le=15)
    sort_policy: Literal["cost_modality_quality", "manual"] = (
        "cost_modality_quality"
    )


class RouteMigrationBody(BaseModel):
    preset_id: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=100)


class CodexDeviceLoginBody(BaseModel):
    account_id: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=100)


class CodexAccountUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    weight: int | None = Field(default=None, ge=1, le=100)


def _setting_command(db: Session) -> SystemSettingCommandService:
    return SystemSettingCommandService(system_setting_repository(db))


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _validate_variation_groups(
    groups: dict[str, dict[str, dict[str, Any]]],
) -> None:
    if len(groups) > 12 or _json_size(groups) > 64 * 1024:
        raise HTTPException(422, "Variation Groups 数量或体积超过上限")
    from core.model_provider.preset_config import validate_variation_patch_map

    for group_name, options in groups.items():
        if not str(group_name).strip() or len(str(group_name)) > 64:
            raise HTTPException(422, "Variation Group 名称无效")
        if not isinstance(options, dict) or len(options) > 16:
            raise HTTPException(422, f"Variation Group {group_name} 选项无效")
        for option_name, patch in options.items():
            if not str(option_name).strip() or len(str(option_name)) > 64:
                raise HTTPException(422, "Variation 选项名称无效")
            if not isinstance(patch, dict) or len(patch) > 32:
                raise HTTPException(
                    422,
                    f"Variation {group_name}/{option_name} patch 无效",
                )
            try:
                validate_variation_patch_map(patch)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc


def _validate_extra_headers(headers: dict[str, str]) -> dict[str, str]:
    from core.model_provider.preset_config import SENSITIVE_HEADER_NAMES

    if len(headers) > 32 or _json_size(headers) > 16 * 1024:
        raise HTTPException(422, "Extra Headers 数量或体积超过上限")
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        header_name = str(name or "").strip()
        if not header_name or len(header_name) > 128:
            raise HTTPException(422, "Extra Header 名称无效")
        if header_name.lower() in SENSITIVE_HEADER_NAMES:
            raise HTTPException(
                422,
                f"{header_name} 属于认证 Header，请在 Provider 凭据中配置",
            )
        text = str(value or "")
        if "\r" in text or "\n" in text or len(text) > 2048:
            raise HTTPException(422, f"Extra Header {header_name} 值无效")
        normalized[header_name] = text
    return normalized


def _validate_driver_options(
    driver_type: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    allowed = {
        "openai": {"echo_reasoning"},
        "anthropic": {"auth_as_bearer", "disable_prompt_caching"},
        "codex": set(),
    }[driver_type]
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise HTTPException(
            422,
            f"{driver_type} Driver 不支持参数: {', '.join(unknown)}",
        )
    return {key: bool(value) for key, value in options.items()}


def _preset_from_body(
    preset_id: str,
    body: ModelPresetWriteBody,
    db: Session,
):
    from core.model_provider.preset_config import ModelPreset, validate_preset_id
    from core.model_provider.provider_config import (
        canonical_provider_instance_id,
        get_provider_instance,
    )

    try:
        normalized_id = validate_preset_id(preset_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    provider_id = canonical_provider_instance_id(body.provider_id)
    provider = get_provider_instance(provider_id, db)
    if provider is None:
        raise HTTPException(422, f"Provider 不存在: {body.provider_id}")
    model = str(body.model or "").strip()
    if not model:
        raise HTTPException(422, "Model ID 不能为空")
    if body.max_output > body.max_context:
        raise HTTPException(422, "max_output 不能大于 max_context")
    if body.retry_policy.max_delay < body.retry_policy.base_delay:
        raise HTTPException(422, "retry.max_delay 不能小于 base_delay")
    if _json_size(body.extra_body) > 64 * 1024:
        raise HTTPException(422, "Extra Body 超过 64 KiB")
    _validate_variation_groups(body.variation_groups)
    extra_headers = _validate_extra_headers(body.extra_headers)
    driver_options = _validate_driver_options(
        provider.driver_type,
        body.driver_options,
    )
    reasoning_effort = str(body.reasoning_effort or "").strip().lower()
    if provider.driver_type == "anthropic" and reasoning_effort:
        raise HTTPException(422, "Anthropic Driver 不接受 reasoning_effort")
    if provider.driver_type == "codex":
        if body.temperature is not None:
            raise HTTPException(422, "Codex Responses Driver 不接受 temperature")
        if body.extra_body or body.extra_headers:
            raise HTTPException(
                422,
                "Codex Driver 的请求体由 KT 管理，不接受 Extra Body/Headers",
            )
        if reasoning_effort not in {
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            raise HTTPException(422, "Codex reasoning_effort 无效")
    if provider.driver_type != "openai" and body.enable_thinking != "auto":
        raise HTTPException(
            422,
            "enable_thinking 只适用于 OpenAI-compatible Driver",
        )
    allowed_capabilities = {
        "supports_stream",
        "supports_tools",
        "supports_image",
    }
    unknown_capabilities = sorted(set(body.capabilities) - allowed_capabilities)
    if unknown_capabilities:
        raise HTTPException(
            422,
            f"未知模型能力: {', '.join(unknown_capabilities)}",
        )
    capabilities = {
        key: bool(body.capabilities.get(key, default))
        for key, default in {
            "supports_stream": True,
            "supports_tools": True,
            "supports_image": False,
        }.items()
    }
    return ModelPreset(
        id=normalized_id,
        display_name=str(body.display_name or normalized_id).strip() or normalized_id,
        provider_id=provider_id,
        model=model,
        enabled=body.enabled,
        max_context=body.max_context,
        max_output=body.max_output,
        temperature=body.temperature,
        reasoning_effort=reasoning_effort,
        service_tier=str(body.service_tier or "").strip(),
        cost_input_1m=body.cost_input_1m,
        cost_output_1m=body.cost_output_1m,
        intelligence=body.intelligence,
        fallback_only=body.fallback_only,
        timeout=body.timeout,
        enable_thinking=body.enable_thinking,
        capabilities=capabilities,
        extra_headers=extra_headers,
        extra_body=dict(body.extra_body),
        retry_policy=body.retry_policy.model_dump(),
        variation_groups=dict(body.variation_groups),
        driver_options=driver_options,
        input_modalities=tuple(body.input_modalities),
        output_modalities=tuple(body.output_modalities),
        supported_endpoints=tuple(body.supported_endpoints),
    )


def _preset_view(preset: object, db: Session) -> dict[str, Any]:
    from core.model_provider.preset_config import preset_route_references
    from core.model_provider.provider_config import get_provider_instance

    provider = get_provider_instance(preset.provider_id, db)
    return {
        **preset.public_view(provider),
        "route_references": preset_route_references(preset.id, db),
    }


def _model_default_view(model_default: object, db: Session) -> dict[str, Any]:
    from core.model_provider.preset_config import (
        model_default_route_references,
    )
    from core.model_provider.provider_config import get_provider_instance

    provider = get_provider_instance(model_default.provider_id, db)
    return {
        **model_default.public_view(provider),
        "route_references": model_default_route_references(
            model_default.provider_id,
            model_default.model,
            db,
        ),
    }


@router.get("/models/defaults")
def list_model_defaults_api(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        list_model_defaults,
        model_driver_schemas,
    )

    return {
        "defaults": [
            _model_default_view(item, db) for item in list_model_defaults(db)
        ],
        "driver_schemas": model_driver_schemas(),
    }


@router.put("/models/defaults")
def upsert_model_default(
    body: ModelPresetWriteBody,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        ensure_model_supports_route,
        model_default_id,
        model_default_route_references,
        model_default_write,
    )
    from core.model_provider.route_registry import require_model_route_descriptor
    from core.settings_service import settings

    default = _preset_from_body(
        model_default_id(body.provider_id, body.model),
        body,
        db,
    )
    for route_key in model_default_route_references(
        default.provider_id,
        default.model,
        db,
    ):
        try:
            ensure_model_supports_route(
                default,
                require_model_route_descriptor(route_key),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    _setting_command(db).upsert_many((model_default_write(default),))
    settings.invalidate()
    audit(
        db,
        "upsert_model_default",
        "model_default",
        f"{default.provider_id}/{default.model}",
        {
            "provider_id": default.provider_id,
            "model": default.model,
            "fallback_only": default.fallback_only,
        },
        ip_address=client_ip(request),
    )
    return {"ok": True, "model_default": _model_default_view(default, db)}


@router.delete("/models/defaults")
def delete_model_default(
    provider_id: str,
    model: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        get_model_default,
        model_default_route_references,
        model_default_setting_key,
    )
    from core.settings_service import settings

    default = get_model_default(provider_id, model, db)
    if default is None:
        raise HTTPException(404, "模型默认配置不存在")
    references = model_default_route_references(provider_id, model, db)
    if references:
        raise HTTPException(
            409,
            detail={
                "message": "模型正被 Route Binding 引用，不能删除默认配置",
                "route_references": references,
            },
        )
    deleted = _setting_command(db).delete_many((
        model_default_setting_key(provider_id, model),
    ))
    settings.invalidate()
    audit(
        db,
        "delete_model_default",
        "model_default",
        f"{provider_id}/{model}",
        {"deleted": deleted},
        ip_address=client_ip(request),
    )
    return {"ok": True, "provider_id": provider_id, "model": model}


@router.get("/models/presets")
def list_model_presets_api(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        list_model_presets,
        model_driver_schemas,
    )

    return {
        "presets": [_preset_view(preset, db) for preset in list_model_presets(db)],
        "driver_schemas": model_driver_schemas(),
    }


@router.post("/models/presets", status_code=201)
def create_model_preset(
    body: ModelPresetCreateBody,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        get_model_preset,
        model_preset_write,
    )
    from core.settings_service import settings

    preset = _preset_from_body(body.id, body, db)
    if get_model_preset(preset.id, db) is not None:
        raise HTTPException(409, f"Model Preset 已存在: {preset.id}")
    _setting_command(db).upsert_many((model_preset_write(preset),))
    settings.invalidate()
    audit(
        db,
        "create_model_preset",
        "model_preset",
        preset.id,
        {"provider_id": preset.provider_id, "model": preset.model},
        ip_address=client_ip(request),
    )
    stored = get_model_preset(preset.id, db)
    return {"ok": True, "preset": _preset_view(stored, db)}


@router.put("/models/presets/{preset_id}")
def update_model_preset(
    preset_id: str,
    body: ModelPresetWriteBody,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        get_model_preset,
        model_preset_write,
    )
    from core.settings_service import settings

    if get_model_preset(preset_id, db) is None:
        raise HTTPException(404, f"Model Preset 不存在: {preset_id}")
    preset = _preset_from_body(preset_id, body, db)
    _setting_command(db).upsert_many((model_preset_write(preset),))
    settings.invalidate()
    audit(
        db,
        "update_model_preset",
        "model_preset",
        preset.id,
        {"provider_id": preset.provider_id, "model": preset.model},
        ip_address=client_ip(request),
    )
    stored = get_model_preset(preset.id, db)
    return {"ok": True, "preset": _preset_view(stored, db)}


@router.delete("/models/presets/{preset_id}")
def delete_model_preset(
    preset_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        get_model_preset,
        preset_route_references,
        preset_setting_key,
    )
    from core.settings_service import settings

    preset = get_model_preset(preset_id, db)
    if preset is None:
        raise HTTPException(404, f"Model Preset 不存在: {preset_id}")
    references = preset_route_references(preset.id, db)
    if references:
        raise HTTPException(
            409,
            detail={
                "message": "Model Preset 正被 Route Binding 引用，不能删除",
                "route_references": references,
            },
        )
    deleted = _setting_command(db).delete_many((preset_setting_key(preset.id),))
    settings.invalidate()
    audit(
        db,
        "delete_model_preset",
        "model_preset",
        preset.id,
        {"deleted": deleted},
        ip_address=client_ip(request),
    )
    return {"ok": True, "preset_id": preset.id}


@router.post("/models/presets/{preset_id}/resolve")
def resolve_model_preset_api(
    preset_id: str,
    body: PresetResolveBody,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        build_request_preview,
        get_model_preset,
        resolve_model_preset,
    )
    from core.model_provider.provider_config import get_provider_instance

    preset = get_model_preset(preset_id, db)
    if preset is None:
        raise HTTPException(404, f"Model Preset 不存在: {preset_id}")
    provider = get_provider_instance(preset.provider_id, db)
    if provider is None:
        raise HTTPException(409, f"Provider 不存在: {preset.provider_id}")
    try:
        resolved = resolve_model_preset(preset, body.selected_variations)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "resolved": resolved.public_view(provider),
        "request_preview": build_request_preview(resolved, provider),
    }


def _runtime_plan(preset: object, provider: object):
    from core.model_provider.route_plan import ReplyRoutePlan

    descriptor = provider.descriptor
    return ReplyRoutePlan(
        provider_id=provider.id,
        registry_provider=provider.registry_provider or provider.id,
        base_url=provider.base_url,
        api_key=provider.api_key,
        timeout=preset.timeout,
        driver_type=provider.driver_type,
        request_protocol=descriptor.request_protocol.value,
        request_path=descriptor.request_path,
        profile_id=preset.id,
        model=preset.model,
        temperature=preset.temperature,
        max_tokens=preset.max_output,
        max_context=preset.max_context,
        cost_input_1m=preset.cost_input_1m,
        cost_output_1m=preset.cost_output_1m,
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


@router.post("/models/presets/{preset_id}/test")
async def test_model_preset(
    preset_id: str,
    body: PresetTestBody,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        get_model_preset,
        resolve_model_preset,
    )
    from core.model_provider.provider_config import get_provider_instance
    from core.model_provider.admin_runtime import probe_model_preset
    from foundation.llm.safe_diagnostics import safe_response_summary

    preset = get_model_preset(preset_id, db)
    if preset is None:
        raise HTTPException(404, f"Model Preset 不存在: {preset_id}")
    provider = get_provider_instance(preset.provider_id, db)
    if provider is None:
        raise HTTPException(409, f"Provider 不存在: {preset.provider_id}")
    if not preset.enabled or not provider.enabled:
        return {"ok": False, "status": "disabled", "error": "Preset 或 Provider 已禁用"}
    if not provider.credential_configured:
        return {
            "ok": False,
            "status": "auth_required",
            "error": "Provider 凭据未配置",
        }
    try:
        resolved = resolve_model_preset(preset, body.selected_variations)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    started = time.monotonic()
    try:
        response = await probe_model_preset(
            _runtime_plan(resolved.preset, provider),
            prompt=body.prompt,
        )
        return {
            "ok": True,
            "status": "ready",
            "preset_id": preset.id,
            "provider_id": provider.id,
            "driver_type": provider.driver_type,
            "model": resolved.preset.model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "output": str(response.content or "")[:500],
            "usage": dict(response.usage or {}),
        }
    except Exception as exc:
        error = safe_response_summary(exc, max_chars=500)
        if provider.api_key:
            error = error.replace(provider.api_key, "[REDACTED]")
        return {
            "ok": False,
            "status": "failed",
            "preset_id": preset.id,
            "driver_type": provider.driver_type,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": error,
        }


def _binding_candidate_views(route_key: str, db: Session) -> list[dict[str, Any]]:
    from core.model_provider.preset_config import resolve_route_binding_candidates
    from core.model_provider.provider_config import get_provider_instance

    try:
        resolved = resolve_route_binding_candidates(route_key, db)
    except ValueError:
        return []
    views = []
    for candidate, item in resolved:
        provider = get_provider_instance(item.preset.provider_id, db)
        views.append({
            **candidate.to_dict(),
            "model": item.preset.model,
            "provider_id": item.preset.provider_id,
            "driver_type": getattr(provider, "driver_type", ""),
            "model_enabled": item.preset.enabled,
            "provider_enabled": bool(getattr(provider, "enabled", False)),
            "cost_input_1m": item.preset.cost_input_1m,
            "cost_output_1m": item.preset.cost_output_1m,
            "intelligence": item.preset.intelligence,
            "fallback_only": item.preset.fallback_only,
            "input_modalities": list(item.preset.input_modalities),
            "output_modalities": list(item.preset.output_modalities),
            "route_overrides": dict(item.route_overrides),
        })
    return views


@router.get("/models/bindings")
def list_model_bindings(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from clients.classifier_client import resolve_model_route
    from core.model_provider.preset_config import (
        get_effective_route_binding,
        get_route_binding,
    )
    from core.model_provider.route_registry import list_model_route_descriptors
    from core.model_provider.contracts import ProviderCapability

    items = []
    for descriptor in list_model_route_descriptors():
        direct = get_route_binding(descriptor.route_key, db)
        effective = get_effective_route_binding(descriptor.route_key, db)
        legacy = resolve_model_route(descriptor.route_key)
        items.append({
            "route_key": descriptor.route_key,
            "label": descriptor.label,
            "route_type": descriptor.route_type,
            "owner": descriptor.owner,
            "domain": descriptor.domain,
            "binding": direct.public_view() if direct is not None else None,
            "effective_binding": (
                effective.public_view() if effective is not None else None
            ),
            "resolved_candidates": _binding_candidate_views(
                descriptor.route_key,
                db,
            ),
            "supported_driver_types": (
                ["openai", "anthropic", "codex"]
                if descriptor.route_key == "reply"
                else ["openai"]
            ),
            "required_model_capabilities": {
                "supports_image": (
                    ProviderCapability.VISION
                    in descriptor.required_provider_capabilities
                ),
                "supports_stream": (
                    ProviderCapability.STREAMING
                    in descriptor.required_provider_capabilities
                ),
                "supports_tools": (
                    ProviderCapability.TOOL_CALLING
                    in descriptor.required_provider_capabilities
                ),
            },
            "required_input_modalities": (
                ["image"]
                if ProviderCapability.VISION
                in descriptor.required_provider_capabilities
                else []
            ),
            "legacy": {
                "provider_id": legacy.get("provider_id"),
                "profile_id": legacy.get("profile_id"),
                "model": legacy.get("model"),
                "driver_type": legacy.get("driver_type"),
                "source": legacy.get("source"),
                "binding_error": legacy.get("binding_error"),
            },
        })
    return {"bindings": items}


@router.put("/models/bindings/{route_key}")
def update_model_binding(
    route_key: str,
    body: RouteBindingBody,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        ModelRouteBinding,
        ModelRouteBindingCandidate,
        ensure_model_supports_route,
        get_model_default,
        get_model_preset,
        resolve_model_default,
        resolve_model_preset,
        route_binding_write,
    )
    from core.model_provider.provider_config import (
        canonical_provider_instance_id,
        get_provider_instance,
    )
    from core.model_provider.route_registry import require_model_route_descriptor
    from core.settings_service import settings

    try:
        descriptor = require_model_route_descriptor(route_key)
    except Exception as exc:
        raise HTTPException(404, f"Route 不存在: {route_key}") from exc
    seen: set[str] = set()
    candidates = []
    for item in body.candidates:
        if item.provider_id or item.model:
            if not item.provider_id or not item.model:
                raise HTTPException(422, "Provider 与 Model 必须同时配置")
            provider_id = canonical_provider_instance_id(item.provider_id)
            model_default = get_model_default(provider_id, item.model, db)
            if model_default is None:
                raise HTTPException(
                    422,
                    f"模型没有默认配置: {provider_id}/{item.model}",
                )
            identity = f"{provider_id}/{item.model}"
            if identity in seen:
                raise HTTPException(422, f"模型重复: {identity}")
            seen.add(identity)
            provider = get_provider_instance(provider_id, db)
            if provider is None:
                raise HTTPException(422, f"Provider 不存在: {provider_id}")
            if (
                descriptor.route_key != "reply"
                and not provider.route_completion_supported
            ):
                raise HTTPException(
                    422,
                    f"Route {descriptor.route_key} 目前只支持 OpenAI-compatible 模型",
                )
            overrides = item.overrides.model_dump(
                exclude_unset=True,
                exclude_none=True,
            )
            if "retry_policy" in overrides:
                retry = overrides["retry_policy"]
                if retry["max_delay"] < retry["base_delay"]:
                    raise HTTPException(422, "retry.max_delay 不能小于 base_delay")
            if "extra_headers" in overrides:
                overrides["extra_headers"] = _validate_extra_headers(
                    overrides["extra_headers"]
                )
            if "extra_body" in overrides and _json_size(
                overrides["extra_body"]
            ) > 64 * 1024:
                raise HTTPException(422, "Extra Body 超过 64 KiB")
            if "driver_options" in overrides:
                overrides["driver_options"] = _validate_driver_options(
                    provider.driver_type,
                    overrides["driver_options"],
                )
            candidate = ModelRouteBindingCandidate(
                provider_id=provider_id,
                model=model_default.model,
                overrides=overrides,
            )
            try:
                resolved = resolve_model_default(model_default, overrides)
                ensure_model_supports_route(resolved.preset, descriptor)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            candidates.append(candidate)
            continue
        if not item.preset_id:
            raise HTTPException(422, "候选必须选择模型")
        preset = get_model_preset(item.preset_id, db)
        if preset is None:
            raise HTTPException(422, f"Model Preset 不存在: {item.preset_id}")
        if preset.id in seen:
            raise HTTPException(422, f"Preset 重复: {preset.id}")
        seen.add(preset.id)
        provider = get_provider_instance(preset.provider_id, db)
        if provider is None:
            raise HTTPException(422, f"Provider 不存在: {preset.provider_id}")
        if descriptor.route_key != "reply" and not provider.route_completion_supported:
            raise HTTPException(
                422,
                f"Route {descriptor.route_key} 目前只支持 OpenAI-compatible Preset",
            )
        try:
            resolved = resolve_model_preset(preset, item.selected_variations)
            ensure_model_supports_route(resolved.preset, descriptor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        candidates.append(ModelRouteBindingCandidate(
            preset_id=preset.id,
            selected_variations=dict(item.selected_variations),
        ))
    binding = ModelRouteBinding(
        route_key=descriptor.route_key,
        candidates=tuple(candidates),
        min_intelligence=body.min_intelligence,
        sort_policy=body.sort_policy,
    )
    _setting_command(db).upsert_many((route_binding_write(binding),))
    settings.invalidate()
    audit(
        db,
        "update_model_binding",
        "model_route_binding",
        descriptor.route_key,
        {
            "models": [item.identity for item in candidates],
            "min_intelligence": binding.min_intelligence,
            "sort_policy": binding.sort_policy,
        },
        ip_address=client_ip(request),
    )
    return {
        "ok": True,
        "binding": binding.public_view(),
        "resolved_candidates": _binding_candidate_views(descriptor.route_key, db),
    }


@router.delete("/models/bindings/{route_key}")
def delete_model_binding(
    route_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.preset_config import (
        get_route_binding,
        route_binding_setting_key,
    )
    from core.settings_service import settings

    binding = get_route_binding(route_key, db)
    if binding is None:
        raise HTTPException(404, f"Route Binding 不存在: {route_key}")
    _setting_command(db).delete_many((route_binding_setting_key(route_key),))
    settings.invalidate()
    audit(
        db,
        "delete_model_binding",
        "model_route_binding",
        route_key,
        {},
        ip_address=client_ip(request),
    )
    return {"ok": True, "route_key": route_key}


@router.post("/models/routes/{route_key}/migrate-to-preset")
def migrate_route_to_preset(
    route_key: str,
    body: RouteMigrationBody,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from clients.classifier_client import resolve_model_route
    from clients.model_registry import registry
    from core.model_provider.preset_config import (
        ModelPreset,
        ModelRouteBinding,
        ModelRouteBindingCandidate,
        get_model_preset,
        model_preset_write,
        route_binding_write,
        validate_preset_id,
    )
    from core.model_provider.provider_config import get_provider_instance
    from core.model_provider.route_registry import require_model_route_descriptor
    from core.settings_service import settings

    try:
        descriptor = require_model_route_descriptor(route_key)
        preset_id = validate_preset_id(body.preset_id or f"route-{descriptor.route_key}")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if get_model_preset(preset_id, db) is not None:
        raise HTTPException(409, f"Model Preset 已存在: {preset_id}")
    route = resolve_model_route(descriptor.route_key)
    provider = get_provider_instance(str(route.get("provider_id") or ""), db)
    if provider is None:
        raise HTTPException(409, "当前 Route 的 Provider 不存在")
    model = str(route.get("model") or "").strip()
    if not model or model == "未指定":
        raise HTTPException(409, "当前 Route 没有可迁移的 Model ID")
    model_info = registry.get_model_info(model) or {}
    preset = ModelPreset(
        id=preset_id,
        display_name=str(body.display_name or descriptor.label).strip()
        or descriptor.label,
        provider_id=provider.id,
        model=model,
        max_context=int(model_info.get("context_window") or 128000),
        max_output=max(1, int(route.get("max_tokens") or 16384)),
        temperature=route.get("temperature"),
        cost_input_1m=model_info.get("cost_input_1m"),
        cost_output_1m=model_info.get("cost_output_1m"),
        timeout=float(route.get("timeout") or 120),
        enable_thinking=str(route.get("enable_thinking") or "auto"),
        capabilities={
            "supports_stream": bool(model_info.get("supports_stream", True)),
            "supports_tools": bool(model_info.get("supports_tools", True)),
            "supports_image": bool(model_info.get("supports_image", False)),
        },
        retry_policy=RetryPolicyBody().model_dump(),
    )
    binding = ModelRouteBinding(
        route_key=descriptor.route_key,
        candidates=(ModelRouteBindingCandidate(preset_id=preset.id),),
    )
    _setting_command(db).upsert_many((
        model_preset_write(preset),
        route_binding_write(binding),
    ))
    settings.invalidate()
    audit(
        db,
        "migrate_route_to_preset",
        "model_route_binding",
        descriptor.route_key,
        {"preset_id": preset.id},
        ip_address=client_ip(request),
    )
    return {
        "ok": True,
        "preset": _preset_view(preset, db),
        "binding": binding.public_view(),
    }


@router.get("/models/kt/native-tools")
def list_kt_native_tools(_auth=Depends(verify_admin)):
    from core.model_provider.admin_runtime import list_provider_native_tools

    return {"tools": [dict(item) for item in list_provider_native_tools()]}


@router.get("/models/codex/status")
def get_codex_status(_auth=Depends(verify_admin)):
    from core.model_provider.admin_runtime import codex_admin_status

    return dict(codex_admin_status())


@router.get("/models/codex/accounts")
def get_codex_accounts(
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.admin_runtime import list_codex_account_views

    return {
        "accounts": [dict(item) for item in list_codex_account_views(db)],
        "strategy": {
            "mode": "session_sticky_weighted_round_robin",
            "session_sticky": True,
            "failover": "next_account_then_next_model",
            "health_scope": "model_account",
        },
    }


@router.patch("/models/codex/accounts/{account_id}")
def patch_codex_account(
    account_id: str,
    body: CodexAccountUpdateBody,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    if body.name is None and body.enabled is None and body.weight is None:
        raise HTTPException(422, "至少需要修改一个 Codex 账号字段")
    from core.model_provider.admin_runtime import (
        CodexAdminError,
        CodexAdminErrorCode,
        update_codex_account_view,
    )

    try:
        account = update_codex_account_view(
            account_id,
            name=body.name,
            enabled=body.enabled,
            weight=body.weight,
            database=db,
        )
    except CodexAdminError as exc:
        status_code = (
            404
            if exc.code is CodexAdminErrorCode.ACCOUNT_NOT_FOUND
            else 422
        )
        raise HTTPException(status_code, str(exc)) from exc
    audit(
        db,
        "update_codex_account",
        "codex_account",
        str(account.get("id") or account_id),
        {
            "name_changed": body.name is not None,
            "enabled": body.enabled,
            "weight": body.weight,
        },
        ip_address=client_ip(request),
    )
    return {"ok": True, "account": dict(account)}


@router.delete("/models/codex/accounts/{account_id}")
def remove_codex_account(
    account_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.admin_runtime import (
        CodexAdminError,
        delete_codex_account,
    )

    try:
        deleted = delete_codex_account(account_id, database=db)
    except CodexAdminError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not deleted:
        raise HTTPException(404, "Codex 账号不存在")
    audit(
        db,
        "delete_codex_account",
        "codex_account",
        account_id,
        {},
        ip_address=client_ip(request),
    )
    return {"ok": True, "account_id": account_id}


@router.post("/models/codex/device-login")
async def start_codex_device_login(
    request: Request,
    body: CodexDeviceLoginBody | None = None,
    db: Session = Depends(get_db),
    _auth=Depends(verify_admin),
):
    from core.model_provider.admin_runtime import (
        CodexAdminError,
        CodexAdminErrorCode,
        start_codex_device_login as start_login,
    )

    payload = body or CodexDeviceLoginBody()
    try:
        result = await start_login(
            account_id=payload.account_id,
            name=payload.name,
            database=db,
        )
        resolved_account_id = str(
            result.get("account_id") or payload.account_id
        )
        audit(
            db,
            "start_codex_device_login",
            "codex_account",
            resolved_account_id,
            {"new_account": not bool(payload.account_id)},
            ip_address=client_ip(request),
        )
        return dict(result)
    except CodexAdminError as exc:
        status_code = {
            CodexAdminErrorCode.ACCOUNT_NOT_FOUND: 404,
            CodexAdminErrorCode.CREDENTIAL_UNAVAILABLE: 503,
            CodexAdminErrorCode.INVALID_ACCOUNT: 422,
            CodexAdminErrorCode.UPSTREAM_FAILED: 502,
        }[exc.code]
        detail = str(exc)
        if exc.code is CodexAdminErrorCode.UPSTREAM_FAILED:
            detail = f"Codex Device OAuth 启动失败: {detail[:300]}"
        raise HTTPException(status_code, detail) from exc


@router.get("/models/codex/device-login/{login_id}")
async def get_codex_device_login(login_id: str, _auth=Depends(verify_admin)):
    from core.model_provider.admin_runtime import (
        get_codex_device_login as resolve_device_login,
    )

    status = await resolve_device_login(login_id)
    if status is None:
        raise HTTPException(404, "Codex Device OAuth 会话不存在或已过期")
    return dict(status)


@router.get("/models/codex/usage")
async def get_codex_usage(_auth=Depends(verify_admin)):
    from core.model_provider.admin_runtime import (
        CodexAdminError,
        get_codex_usage as resolve_codex_usage,
    )

    try:
        return dict(await resolve_codex_usage())
    except CodexAdminError as exc:
        raise HTTPException(401, f"Codex Usage 获取失败: {str(exc)[:300]}") from exc


__all__ = [
    "CodexAccountUpdateBody",
    "CodexDeviceLoginBody",
    "ModelPresetCreateBody",
    "ModelPresetWriteBody",
    "PresetResolveBody",
    "PresetTestBody",
    "RetryPolicyBody",
    "RouteBindingBody",
    "RouteBindingCandidateBody",
    "RouteMigrationBody",
    "router",
]
