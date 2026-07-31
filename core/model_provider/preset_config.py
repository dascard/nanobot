"""Model Preset 与业务 Route Binding 控制面。

Provider 只保存连接、认证和 KT Driver 身份；Preset 保存模型请求参数；
Binding 只描述业务 Route 采用哪些 Preset 及其 fallback 顺序。三层配置分别
持久化，避免把连接凭据、模型参数和业务路由重新揉成一个大对象。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

from core.db import system_setting_repository
from core.settings_admin_service import SystemSettingWrite


PRESET_SETTING_PREFIX = "model.presets."
ROUTE_BINDING_SETTING_PREFIX = "model.bindings."
PRESET_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
RETRY_CLASSES = frozenset({"rate_limit", "server", "transient", "overflow"})
SENSITIVE_HEADER_NAMES = frozenset({
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "cookie",
    "set-cookie",
})


def validate_preset_id(preset_id: str) -> str:
    value = str(preset_id or "").strip()
    if not PRESET_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "Preset ID 必须以小写字母开头，且只能包含小写字母、数字、_、-，"
            "长度不超过 64"
        )
    return value


def preset_setting_key(preset_id: str) -> str:
    return f"{PRESET_SETTING_PREFIX}{validate_preset_id(preset_id)}"


def route_binding_setting_key(route_key: str) -> str:
    value = str(route_key or "").strip()
    if not value or len(value) > 128:
        raise ValueError("Route key 无效")
    return f"{ROUTE_BINDING_SETTING_PREFIX}{value}"


def _finite_float(value: object, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _string_map(value: object) -> dict[str, str]:
    return {
        str(key): str(item)
        for key, item in _json_object(value).items()
        if str(key or "").strip() and str(item or "").strip()
    }


@dataclass(frozen=True, slots=True)
class ModelPreset:
    id: str
    display_name: str
    provider_id: str
    model: str
    enabled: bool = True
    max_context: int = 128000
    max_output: int = 16384
    temperature: float | None = None
    reasoning_effort: str = ""
    service_tier: str = ""
    timeout: float = 120.0
    enable_thinking: str = "auto"
    capabilities: dict[str, bool] = field(default_factory=lambda: {
        "supports_stream": True,
        "supports_tools": True,
        "supports_image": False,
    })
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(default_factory=dict)
    variation_groups: dict[str, dict[str, dict[str, Any]]] = field(
        default_factory=dict
    )
    driver_options: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""

    def to_storage(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "provider_id": self.provider_id,
            "model": self.model,
            "enabled": self.enabled,
            "max_context": self.max_context,
            "max_output": self.max_output,
            "temperature": self.temperature,
            "reasoning_effort": self.reasoning_effort,
            "service_tier": self.service_tier,
            "timeout": self.timeout,
            "enable_thinking": self.enable_thinking,
            "capabilities": dict(self.capabilities),
            "extra_headers": dict(self.extra_headers),
            "extra_body": dict(self.extra_body),
            "retry_policy": dict(self.retry_policy),
            "variation_groups": dict(self.variation_groups),
            "driver_options": dict(self.driver_options),
        }

    def public_view(self, provider: object | None = None) -> dict[str, Any]:
        driver_type = str(getattr(provider, "driver_type", "") or "")
        provider_enabled = bool(getattr(provider, "enabled", False))
        credential_configured = bool(
            getattr(provider, "credential_configured", False)
        )
        return {
            "id": self.id,
            **self.to_storage(),
            "driver_type": driver_type,
            "provider_enabled": provider_enabled,
            "credential_configured": credential_configured,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_storage(
        cls,
        preset_id: str,
        data: dict[str, Any],
        *,
        updated_at: str = "",
    ) -> "ModelPreset":
        temperature_raw = data.get("temperature")
        temperature = (
            None
            if temperature_raw in (None, "")
            else _finite_float(temperature_raw, 0.7)
        )
        thinking = str(data.get("enable_thinking") or "auto").strip().lower()
        if thinking not in {"auto", "true", "false"}:
            thinking = "auto"
        return cls(
            id=validate_preset_id(preset_id),
            display_name=str(data.get("display_name") or preset_id).strip()[:100]
            or preset_id,
            provider_id=str(data.get("provider_id") or "").strip(),
            model=str(data.get("model") or "").strip(),
            enabled=bool(data.get("enabled", True)),
            max_context=_positive_int(data.get("max_context"), 128000),
            max_output=_positive_int(data.get("max_output"), 16384),
            temperature=temperature,
            reasoning_effort=str(data.get("reasoning_effort") or "").strip(),
            service_tier=str(data.get("service_tier") or "").strip(),
            timeout=max(0.1, _finite_float(data.get("timeout"), 120.0)),
            enable_thinking=thinking,
            capabilities={
                key: bool(value)
                for key, value in _json_object(data.get("capabilities")).items()
                if key in {
                    "supports_stream",
                    "supports_tools",
                    "supports_image",
                }
            } or {
                "supports_stream": True,
                "supports_tools": True,
                "supports_image": False,
            },
            extra_headers=_string_map(data.get("extra_headers")),
            extra_body=_json_object(data.get("extra_body")),
            retry_policy=_json_object(data.get("retry_policy")),
            variation_groups=_json_object(data.get("variation_groups")),
            driver_options=_json_object(data.get("driver_options")),
            updated_at=updated_at,
        )


@dataclass(frozen=True, slots=True)
class ResolvedModelPreset:
    preset: ModelPreset
    selected_variations: dict[str, str] = field(default_factory=dict)

    def public_view(self, provider: object | None = None) -> dict[str, Any]:
        return {
            **self.preset.public_view(provider),
            "selected_variations": dict(self.selected_variations),
        }


@dataclass(frozen=True, slots=True)
class ModelRouteBindingCandidate:
    preset_id: str
    selected_variations: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "selected_variations": dict(self.selected_variations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelRouteBindingCandidate":
        return cls(
            preset_id=validate_preset_id(str(data.get("preset_id") or "")),
            selected_variations=_string_map(data.get("selected_variations")),
        )


@dataclass(frozen=True, slots=True)
class ModelRouteBinding:
    route_key: str
    candidates: tuple[ModelRouteBindingCandidate, ...]
    inherited_from: str = ""
    updated_at: str = ""

    def to_storage(self) -> dict[str, Any]:
        return {"candidates": [candidate.to_dict() for candidate in self.candidates]}

    def public_view(self) -> dict[str, Any]:
        return {
            "route_key": self.route_key,
            **self.to_storage(),
            "inherited_from": self.inherited_from or None,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_storage(
        cls,
        route_key: str,
        data: dict[str, Any],
        *,
        updated_at: str = "",
    ) -> "ModelRouteBinding":
        raw_candidates = data.get("candidates")
        if not isinstance(raw_candidates, list):
            raw_candidates = []
        candidates = tuple(
            ModelRouteBindingCandidate.from_dict(item)
            for item in raw_candidates
            if isinstance(item, dict)
        )
        return cls(
            route_key=str(route_key or "").strip(),
            candidates=candidates,
            updated_at=updated_at,
        )


def _load_rows(db: Any | None) -> tuple[dict[str, Any], Any | None]:
    if db is not None:
        rows = system_setting_repository(db).list_all()
        return {row.key: row for row in rows}, None
    from core.database import SessionLocal

    owned_db = SessionLocal()
    try:
        rows = system_setting_repository(owned_db).list_all()
        return {row.key: row for row in rows}, owned_db
    except BaseException:
        owned_db.close()
        raise


def _row_timestamp(row: object) -> str:
    updated_at = getattr(row, "updated_at", None)
    return updated_at.isoformat() if updated_at is not None else ""


def list_model_presets(db: Any | None = None) -> list[ModelPreset]:
    row_map, owned_db = _load_rows(db)
    try:
        presets: list[ModelPreset] = []
        for key, row in row_map.items():
            if not key.startswith(PRESET_SETTING_PREFIX):
                continue
            preset_id = key.removeprefix(PRESET_SETTING_PREFIX)
            try:
                data = json.loads(str(row.value or "{}"))
                if not isinstance(data, dict):
                    continue
                presets.append(ModelPreset.from_storage(
                    preset_id,
                    data,
                    updated_at=_row_timestamp(row),
                ))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(presets, key=lambda item: (item.provider_id, item.id))
    finally:
        if owned_db is not None:
            owned_db.close()


def get_model_preset(
    preset_id: str,
    db: Any | None = None,
) -> ModelPreset | None:
    key = preset_setting_key(preset_id)
    row_map, owned_db = _load_rows(db)
    try:
        row = row_map.get(key)
        if row is None:
            return None
        data = json.loads(str(row.value or "{}"))
        if not isinstance(data, dict):
            return None
        return ModelPreset.from_storage(
            preset_id,
            data,
            updated_at=_row_timestamp(row),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    finally:
        if owned_db is not None:
            owned_db.close()


def list_route_bindings(db: Any | None = None) -> list[ModelRouteBinding]:
    row_map, owned_db = _load_rows(db)
    try:
        bindings: list[ModelRouteBinding] = []
        for key, row in row_map.items():
            if not key.startswith(ROUTE_BINDING_SETTING_PREFIX):
                continue
            route_key = key.removeprefix(ROUTE_BINDING_SETTING_PREFIX)
            try:
                data = json.loads(str(row.value or "{}"))
                if isinstance(data, dict):
                    bindings.append(ModelRouteBinding.from_storage(
                        route_key,
                        data,
                        updated_at=_row_timestamp(row),
                    ))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(bindings, key=lambda item: item.route_key)
    finally:
        if owned_db is not None:
            owned_db.close()


def get_route_binding(
    route_key: str,
    db: Any | None = None,
) -> ModelRouteBinding | None:
    key = route_binding_setting_key(route_key)
    row_map, owned_db = _load_rows(db)
    try:
        row = row_map.get(key)
        if row is None:
            return None
        data = json.loads(str(row.value or "{}"))
        if not isinstance(data, dict):
            return None
        return ModelRouteBinding.from_storage(
            route_key,
            data,
            updated_at=_row_timestamp(row),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    finally:
        if owned_db is not None:
            owned_db.close()


def get_effective_route_binding(
    route_key: str,
    db: Any | None = None,
) -> ModelRouteBinding | None:
    direct = get_route_binding(route_key, db)
    if direct is not None:
        return direct
    from core.model_provider.route_registry import require_model_route_descriptor

    descriptor = require_model_route_descriptor(route_key)
    if descriptor.inherits_from is None:
        return None
    parent = get_effective_route_binding(descriptor.inherits_from, db)
    if parent is None:
        return None
    return replace(
        parent,
        route_key=descriptor.route_key,
        inherited_from=descriptor.inherits_from,
    )


def resolve_model_preset(
    preset: ModelPreset,
    selected_variations: dict[str, str] | None = None,
) -> ResolvedModelPreset:
    """通过 Composition Root 注入的解析 Port 计算最终 Preset。"""

    from core.model_provider.preset_runtime import (
        resolve_model_preset_with_runtime,
    )

    resolved = resolve_model_preset_with_runtime(
        preset,
        dict(selected_variations or {}),
    )
    if not isinstance(resolved, ResolvedModelPreset):
        raise TypeError("Model Preset Resolver 返回了无效结果")
    return resolved


def resolve_route_binding_candidates(
    route_key: str,
    db: Any | None = None,
) -> list[tuple[ModelRouteBindingCandidate, ResolvedModelPreset]]:
    binding = get_effective_route_binding(route_key, db)
    if binding is None:
        return []
    resolved: list[tuple[ModelRouteBindingCandidate, ResolvedModelPreset]] = []
    for candidate in binding.candidates:
        preset = get_model_preset(candidate.preset_id, db)
        if preset is None:
            raise ValueError(f"Route 引用了不存在的 Preset: {candidate.preset_id}")
        resolved.append((
            candidate,
            resolve_model_preset(preset, candidate.selected_variations),
        ))
    return resolved


def preset_route_references(
    preset_id: str,
    db: Any | None = None,
) -> list[str]:
    target = validate_preset_id(preset_id)
    return [
        binding.route_key
        for binding in list_route_bindings(db)
        if any(candidate.preset_id == target for candidate in binding.candidates)
    ]


def model_preset_write(preset: ModelPreset) -> SystemSettingWrite:
    return SystemSettingWrite(
        key=preset_setting_key(preset.id),
        value=json.dumps(preset.to_storage(), ensure_ascii=False, separators=(",", ":")),
        description=f"model preset {preset.id}",
    )


def route_binding_write(binding: ModelRouteBinding) -> SystemSettingWrite:
    return SystemSettingWrite(
        key=route_binding_setting_key(binding.route_key),
        value=json.dumps(binding.to_storage(), ensure_ascii=False, separators=(",", ":")),
        description=f"model route binding {binding.route_key}",
    )


def build_request_preview(
    resolved: ResolvedModelPreset,
    provider: object,
) -> dict[str, Any]:
    """生成脱敏的 KT Driver 最终请求形状，不包含任何凭据。"""

    preset = resolved.preset
    driver_type = str(getattr(provider, "driver_type", "openai") or "openai")
    base_url = str(getattr(provider, "base_url", "") or "").rstrip("/")
    body: dict[str, Any] = {
        "model": preset.model,
        "max_tokens": preset.max_output,
    }
    if preset.temperature is not None and driver_type != "codex":
        body["temperature"] = preset.temperature
    if driver_type == "openai":
        body.update(dict(preset.extra_body))
        if preset.reasoning_effort:
            body.setdefault("reasoning_effort", preset.reasoning_effort)
        if preset.service_tier:
            body.setdefault("service_tier", preset.service_tier)
        from core.model_route_options import apply_enable_thinking_to_payload

        apply_enable_thinking_to_payload(
            body,
            preset.model,
            preset.enable_thinking,
        )
        endpoint = f"{base_url}/chat/completions" if base_url else "chat/completions"
    elif driver_type == "anthropic":
        body.update(dict(preset.extra_body))
        if preset.service_tier:
            body.setdefault("service_tier", preset.service_tier)
        endpoint = f"{base_url}/messages" if base_url else "messages"
    else:
        body = {
            "model": preset.model,
            "reasoning": {
                "effort": preset.reasoning_effort or "medium",
            },
            "service_tier": preset.service_tier or None,
            "store": False,
            "stream": True,
        }
        endpoint = "https://chatgpt.com/backend-api/codex/responses"
    return {
        "driver_type": driver_type,
        "endpoint": endpoint,
        "headers": sorted(preset.extra_headers),
        "body": body,
        "runtime": {
            "max_context": preset.max_context,
            "timeout": preset.timeout,
            "retry_policy": dict(preset.retry_policy),
            "driver_options": dict(preset.driver_options),
            "selected_variations": dict(resolved.selected_variations),
        },
    }


def model_driver_schemas() -> list[dict[str, Any]]:
    """返回页面动态表单使用的 KT Driver 能力与参数约束。"""

    from core.model_provider.provider_config import driver_runtime_status

    common = [
        "max_context",
        "max_output",
        "timeout",
        "retry_policy",
        "variation_groups",
    ]
    schemas = [
        {
            "id": "openai",
            "label": "OpenAI-compatible",
            "transport": "Chat Completions",
            "fields": common + [
                "temperature",
                "reasoning_effort",
                "service_tier",
                "enable_thinking",
                "capabilities",
                "extra_headers",
                "extra_body",
                "echo_reasoning",
            ],
            "reasoning_efforts": ["", "none", "minimal", "low", "medium", "high", "xhigh"],
            "service_tiers": ["", "auto", "default", "flex", "priority"],
            "route_support": {"kt_agent": True, "sync_completion": True},
        },
        {
            "id": "anthropic",
            "label": "Anthropic Messages",
            "transport": "Messages API",
            "fields": common + [
                "temperature",
                "service_tier",
                "extra_headers",
                "extra_body",
                "auth_as_bearer",
                "prompt_caching",
            ],
            "reasoning_efforts": [],
            "service_tiers": ["", "auto", "standard_only"],
            "route_support": {"kt_agent": True, "sync_completion": False},
        },
        {
            "id": "codex",
            "label": "Codex OAuth",
            "transport": "Responses API",
            "fields": common + ["reasoning_effort", "service_tier"],
            "reasoning_efforts": ["none", "minimal", "low", "medium", "high", "xhigh"],
            "service_tiers": ["", "auto", "default", "flex", "priority"],
            "route_support": {"kt_agent": True, "sync_completion": False},
        },
    ]
    for schema in schemas:
        available, reason = driver_runtime_status(schema["id"])
        schema["runtime_available"] = available
        schema["runtime_unavailable_reason"] = reason
    return schemas


__all__ = [
    "ModelPreset",
    "ModelRouteBinding",
    "ModelRouteBindingCandidate",
    "ResolvedModelPreset",
    "SENSITIVE_HEADER_NAMES",
    "build_request_preview",
    "get_effective_route_binding",
    "get_model_preset",
    "get_route_binding",
    "list_model_presets",
    "list_route_bindings",
    "model_driver_schemas",
    "model_preset_write",
    "preset_route_references",
    "preset_setting_key",
    "resolve_model_preset",
    "resolve_route_binding_candidates",
    "route_binding_setting_key",
    "route_binding_write",
    "validate_preset_id",
]
