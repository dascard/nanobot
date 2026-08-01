"""模型默认配置与业务 Route Binding 控制面。

Provider 只保存连接、认证和 KT Driver 身份；模型目录默认配置保存模型自身
元数据与默认请求参数；Binding 直接引用 Provider + Model，并仅保存业务 Route
的局部覆盖。旧 Model Preset 仅用于无损迁移和滚动部署兼容。
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any

from core.db import system_setting_repository
from core.settings_admin_service import SystemSettingWrite


PRESET_SETTING_PREFIX = "model.presets."
MODEL_DEFAULT_SETTING_PREFIX = "model.defaults."
ROUTE_BINDING_SETTING_PREFIX = "model.bindings."
PRESET_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
RETRY_CLASSES = frozenset({"rate_limit", "server", "transient", "overflow"})
ROUTE_OVERRIDE_FIELDS = frozenset({
    "max_output",
    "temperature",
    "reasoning_effort",
    "service_tier",
    "timeout",
    "enable_thinking",
    "extra_headers",
    "extra_body",
    "retry_policy",
    "driver_options",
})
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


def _validate_model_identity(provider_id: str, model: str) -> tuple[str, str]:
    provider = str(provider_id or "").strip()
    model_id = str(model or "").strip()
    if not PRESET_ID_PATTERN.fullmatch(provider):
        raise ValueError("Provider ID 无效")
    if not model_id or len(model_id) > 256 or any(
        char in model_id for char in ("\x00", "\r", "\n")
    ):
        raise ValueError("Model ID 无效")
    return provider, model_id


def model_default_id(provider_id: str, model: str) -> str:
    """生成只用于兼容 KT Profile 的稳定内部 ID。"""

    provider, model_id = _validate_model_identity(provider_id, model)
    digest = hashlib.sha256(f"{provider}\x00{model_id}".encode()).hexdigest()[:20]
    return f"model-{digest}"


def model_default_setting_key(provider_id: str, model: str) -> str:
    provider, model_id = _validate_model_identity(provider_id, model)
    digest = hashlib.sha256(model_id.encode()).hexdigest()
    return f"{MODEL_DEFAULT_SETTING_PREFIX}{provider}.{digest}"


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


def _optional_non_negative_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    parsed = _finite_float(value, -1.0)
    return parsed if parsed >= 0 else None


def _positive_int(value: object, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


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


def _string_tuple(value: object, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return default
    normalized: list[str] = []
    for item in value:
        text = str(item or "").strip().lower().lstrip("/")
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized) or default


@dataclass(frozen=True, slots=True)
class ModelPreset:
    id: str
    display_name: str
    provider_id: str
    model: str
    enabled: bool = True
    max_context: int = 128000
    max_output: int = 16384
    temperature: float | None = 1.0
    reasoning_effort: str = ""
    service_tier: str = ""
    cost_input_1m: float | None = None
    cost_output_1m: float | None = None
    intelligence: int = 0
    fallback_only: bool = False
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
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
    supported_endpoints: tuple[str, ...] = ("chat/completions",)
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
            "cost_input_1m": self.cost_input_1m,
            "cost_output_1m": self.cost_output_1m,
            "intelligence": self.intelligence,
            "fallback_only": self.fallback_only,
            "timeout": self.timeout,
            "enable_thinking": self.enable_thinking,
            "capabilities": dict(self.capabilities),
            "extra_headers": dict(self.extra_headers),
            "extra_body": dict(self.extra_body),
            "retry_policy": dict(self.retry_policy),
            "variation_groups": dict(self.variation_groups),
            "driver_options": dict(self.driver_options),
            "input_modalities": list(self.input_modalities),
            "output_modalities": list(self.output_modalities),
            "supported_endpoints": list(self.supported_endpoints),
        }

    def public_view(self, provider: object | None = None) -> dict[str, Any]:
        driver_type = str(getattr(provider, "driver_type", "") or "")
        provider_enabled = bool(getattr(provider, "enabled", False))
        credential_configured = bool(
            getattr(provider, "credential_configured", False)
        )
        if self.cost_input_1m == 0 and self.cost_output_1m == 0:
            price_tags = ["free"]
        elif self.cost_input_1m is not None or self.cost_output_1m is not None:
            price_tags = ["paid"]
        else:
            price_tags = ["price_unknown"]
        return {
            "id": self.id,
            **self.to_storage(),
            "price_tags": price_tags,
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
        temperature_raw = data.get("temperature", 1.0)
        temperature = (
            None
            if temperature_raw in (None, "")
            else _finite_float(temperature_raw, 1.0)
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
            cost_input_1m=_optional_non_negative_float(
                data.get("cost_input_1m")
            ),
            cost_output_1m=_optional_non_negative_float(
                data.get("cost_output_1m")
            ),
            intelligence=_bounded_int(data.get("intelligence"), 0, 0, 15),
            fallback_only=bool(data.get("fallback_only", False)),
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
            input_modalities=_string_tuple(
                data.get("input_modalities"), ("text",)
            ),
            output_modalities=_string_tuple(
                data.get("output_modalities"), ("text",)
            ),
            supported_endpoints=_string_tuple(
                data.get("supported_endpoints"), ("chat/completions",)
            ),
            updated_at=updated_at,
        )


@dataclass(frozen=True, slots=True)
class ResolvedModelPreset:
    preset: ModelPreset
    selected_variations: dict[str, str] = field(default_factory=dict)
    route_overrides: dict[str, Any] = field(default_factory=dict)

    def public_view(self, provider: object | None = None) -> dict[str, Any]:
        return {
            **self.preset.public_view(provider),
            "selected_variations": dict(self.selected_variations),
            "route_overrides": dict(self.route_overrides),
        }


@dataclass(frozen=True, slots=True)
class ModelRouteBindingCandidate:
    provider_id: str = ""
    model: str = ""
    overrides: dict[str, Any] = field(default_factory=dict)
    # 旧字段只用于滚动部署期间读取既有配置。
    preset_id: str = ""
    selected_variations: dict[str, str] = field(default_factory=dict)

    @property
    def uses_model_default(self) -> bool:
        return bool(self.provider_id and self.model)

    @property
    def identity(self) -> str:
        if self.uses_model_default:
            return f"{self.provider_id}/{self.model}"
        return self.preset_id

    def to_dict(self) -> dict[str, Any]:
        if self.uses_model_default:
            return {
                "provider_id": self.provider_id,
                "model": self.model,
                "overrides": dict(self.overrides),
            }
        return {
            "preset_id": self.preset_id,
            "selected_variations": dict(self.selected_variations),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelRouteBindingCandidate":
        provider_id = str(data.get("provider_id") or "").strip()
        model = str(data.get("model") or "").strip()
        if provider_id or model:
            provider_id, model = _validate_model_identity(provider_id, model)
            raw_overrides = _json_object(data.get("overrides"))
            unknown = sorted(set(raw_overrides) - ROUTE_OVERRIDE_FIELDS)
            if unknown:
                raise ValueError(
                    f"Route 覆盖包含不支持字段: {', '.join(unknown)}"
                )
            return cls(
                provider_id=provider_id,
                model=model,
                overrides=raw_overrides,
            )
        return cls(
            preset_id=validate_preset_id(str(data.get("preset_id") or "")),
            selected_variations=_string_map(data.get("selected_variations")),
        )


@dataclass(frozen=True, slots=True)
class ModelRouteBinding:
    route_key: str
    candidates: tuple[ModelRouteBindingCandidate, ...]
    min_intelligence: int = 0
    sort_policy: str = "cost_modality_quality"
    inherited_from: str = ""
    updated_at: str = ""

    def to_storage(self) -> dict[str, Any]:
        return {
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "min_intelligence": self.min_intelligence,
            "sort_policy": self.sort_policy,
        }

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
            min_intelligence=_bounded_int(
                data.get("min_intelligence"), 0, 0, 15
            ),
            sort_policy=(
                "cost_modality_quality"
                if str(data.get("sort_policy") or "cost_modality_quality")
                != "manual"
                else "manual"
            ),
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


def list_model_defaults(db: Any | None = None) -> list[ModelPreset]:
    """列出模型目录的持久默认配置。"""

    row_map, owned_db = _load_rows(db)
    try:
        defaults: list[ModelPreset] = []
        for key, row in row_map.items():
            if not key.startswith(MODEL_DEFAULT_SETTING_PREFIX):
                continue
            try:
                data = json.loads(str(row.value or "{}"))
                if not isinstance(data, dict):
                    continue
                provider_id, model = _validate_model_identity(
                    str(data.get("provider_id") or ""),
                    str(data.get("model") or ""),
                )
                defaults.append(ModelPreset.from_storage(
                    model_default_id(provider_id, model),
                    data,
                    updated_at=_row_timestamp(row),
                ))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(defaults, key=lambda item: (item.provider_id, item.model))
    finally:
        if owned_db is not None:
            owned_db.close()


def get_model_default(
    provider_id: str,
    model: str,
    db: Any | None = None,
) -> ModelPreset | None:
    provider, model_id = _validate_model_identity(provider_id, model)
    key = model_default_setting_key(provider, model_id)
    row_map, owned_db = _load_rows(db)
    try:
        row = row_map.get(key)
        if row is None:
            return None
        data = json.loads(str(row.value or "{}"))
        if not isinstance(data, dict):
            return None
        stored_provider, stored_model = _validate_model_identity(
            str(data.get("provider_id") or ""),
            str(data.get("model") or ""),
        )
        if (stored_provider, stored_model) != (provider, model_id):
            return None
        return ModelPreset.from_storage(
            model_default_id(provider, model_id),
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


def resolve_model_default(
    model_default: ModelPreset,
    overrides: dict[str, Any] | None = None,
) -> ResolvedModelPreset:
    """把 Route 局部覆盖合并到模型默认配置。"""

    patch = dict(overrides or {})
    unknown = sorted(set(patch) - ROUTE_OVERRIDE_FIELDS)
    if unknown:
        raise ValueError(f"Route 覆盖包含不支持字段: {', '.join(unknown)}")
    merged = model_default.to_storage()
    merged.update(patch)
    resolved = ModelPreset.from_storage(
        model_default.id,
        merged,
        updated_at=model_default.updated_at,
    )
    if resolved.max_output > resolved.max_context:
        raise ValueError("Route max_output 不能大于模型 max_context")
    return ResolvedModelPreset(
        preset=resolved,
        route_overrides=patch,
    )


def model_route_compatibility_error(
    model: ModelPreset,
    route_descriptor: object,
) -> str:
    """检查模型自身能力是否满足业务 Route 的硬约束。"""

    from core.model_provider.contracts import ProviderCapability

    required = frozenset(
        getattr(route_descriptor, "required_provider_capabilities", ()) or ()
    )
    endpoints = {
        str(endpoint or "").strip().lower().lstrip("/")
        for endpoint in model.supported_endpoints
    }
    input_modalities = {
        str(modality or "").strip().lower()
        for modality in model.input_modalities
    }
    if (
        ProviderCapability.CHAT_COMPLETION in required
        and "chat/completions" not in endpoints
    ):
        return "不支持 chat/completions"
    if ProviderCapability.VISION in required and (
        not model.capabilities.get("supports_image", False)
        or "image" not in input_modalities
    ):
        return "不支持图像输入"
    if (
        ProviderCapability.STREAMING in required
        and not model.capabilities.get("supports_stream", False)
    ):
        return "不支持流式输出"
    if (
        ProviderCapability.TOOL_CALLING in required
        and not model.capabilities.get("supports_tools", False)
    ):
        return "不支持工具调用"
    return ""


def ensure_model_supports_route(
    model: ModelPreset,
    route_descriptor: object,
) -> None:
    error = model_route_compatibility_error(model, route_descriptor)
    if error:
        route_key = str(getattr(route_descriptor, "route_key", "") or "")
        raise ValueError(
            f"模型 {model.provider_id}/{model.model} 与 Route {route_key} 不兼容："
            f"{error}"
        )


def _candidate_sort_key(
    item: tuple[int, ModelRouteBindingCandidate, ResolvedModelPreset],
    min_intelligence: int,
) -> tuple[int, int, float, int, int, int]:
    index, _candidate, resolved = item
    model = resolved.preset
    is_free = model.cost_input_1m == 0 and model.cost_output_1m == 0
    below_floor = model.intelligence < min_intelligence
    if model.fallback_only or (is_free and below_floor):
        quality_bucket = 2
    elif below_floor:
        quality_bucket = 1
    else:
        quality_bucket = 0
    price_unknown = int(
        model.cost_input_1m is None or model.cost_output_1m is None
    )
    total_price = (
        float(model.cost_input_1m or 0)
        + float(model.cost_output_1m or 0)
        if not price_unknown
        else float("inf")
    )
    modality_count = max(0, len(model.input_modalities) - 1) + max(
        0, len(model.output_modalities) - 1
    )
    return (
        quality_bucket,
        price_unknown,
        total_price,
        modality_count,
        -model.intelligence,
        index,
    )


def order_resolved_binding_candidates(
    candidates: list[tuple[ModelRouteBindingCandidate, ResolvedModelPreset]],
    *,
    min_intelligence: int,
    sort_policy: str = "cost_modality_quality",
) -> list[tuple[ModelRouteBindingCandidate, ResolvedModelPreset]]:
    if sort_policy == "manual":
        return list(candidates)
    indexed = [
        (index, candidate, resolved)
        for index, (candidate, resolved) in enumerate(candidates)
    ]
    indexed.sort(key=lambda item: _candidate_sort_key(item, min_intelligence))
    return [(candidate, resolved) for _index, candidate, resolved in indexed]


def resolve_route_binding_candidates(
    route_key: str,
    db: Any | None = None,
) -> list[tuple[ModelRouteBindingCandidate, ResolvedModelPreset]]:
    binding = get_effective_route_binding(route_key, db)
    if binding is None:
        return []
    from core.model_provider.route_registry import require_model_route_descriptor

    descriptor = require_model_route_descriptor(route_key)
    resolved: list[tuple[ModelRouteBindingCandidate, ResolvedModelPreset]] = []
    incompatible: list[str] = []
    for candidate in binding.candidates:
        if candidate.uses_model_default:
            model_default = get_model_default(
                candidate.provider_id,
                candidate.model,
                db,
            )
            if model_default is None:
                raise ValueError(
                    "Route 引用了没有默认配置的模型: "
                    f"{candidate.provider_id}/{candidate.model}"
                )
            item = resolve_model_default(model_default, candidate.overrides)
            error = model_route_compatibility_error(item.preset, descriptor)
            if error:
                incompatible.append(f"{candidate.identity}: {error}")
            else:
                resolved.append((candidate, item))
            continue
        preset = get_model_preset(candidate.preset_id, db)
        if preset is None:
            raise ValueError(f"Route 引用了不存在的旧 Preset: {candidate.preset_id}")
        item = resolve_model_preset(preset, candidate.selected_variations)
        error = model_route_compatibility_error(item.preset, descriptor)
        if error:
            incompatible.append(f"{candidate.identity}: {error}")
        else:
            resolved.append((candidate, item))
    if binding.candidates and not resolved:
        detail = "；".join(incompatible) or "没有可用候选"
        raise ValueError(f"Route {route_key} 没有满足硬能力约束的候选：{detail}")
    return order_resolved_binding_candidates(
        resolved,
        min_intelligence=binding.min_intelligence,
        sort_policy=binding.sort_policy,
    )


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


def model_default_route_references(
    provider_id: str,
    model: str,
    db: Any | None = None,
) -> list[str]:
    target = _validate_model_identity(provider_id, model)
    return [
        binding.route_key
        for binding in list_route_bindings(db)
        if any(
            candidate.uses_model_default
            and (candidate.provider_id, candidate.model) == target
            for candidate in binding.candidates
        )
    ]


def model_preset_write(preset: ModelPreset) -> SystemSettingWrite:
    return SystemSettingWrite(
        key=preset_setting_key(preset.id),
        value=json.dumps(preset.to_storage(), ensure_ascii=False, separators=(",", ":")),
        description=f"model preset {preset.id}",
    )


def model_default_write(model_default: ModelPreset) -> SystemSettingWrite:
    provider_id, model = _validate_model_identity(
        model_default.provider_id,
        model_default.model,
    )
    return SystemSettingWrite(
        key=model_default_setting_key(provider_id, model),
        value=json.dumps(
            model_default.to_storage(),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        description=f"model defaults {provider_id}/{model}",
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
            "pricing": {
                "currency": "USD",
                "unit": "1M tokens",
                "input": preset.cost_input_1m,
                "output": preset.cost_output_1m,
            },
            "retry_policy": dict(preset.retry_policy),
            "driver_options": dict(preset.driver_options),
            "selected_variations": dict(resolved.selected_variations),
            "route_overrides": dict(resolved.route_overrides),
        },
    }


def model_driver_schemas() -> list[dict[str, Any]]:
    """返回页面动态表单使用的 KT Driver 能力与参数约束。"""

    from core.model_provider.provider_config import driver_runtime_status

    common = [
        "max_context",
        "max_output",
        "cost_input_1m",
        "cost_output_1m",
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
            "reasoning_efforts": [
                "",
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ],
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
            "reasoning_efforts": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
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
    "MODEL_DEFAULT_SETTING_PREFIX",
    "ROUTE_OVERRIDE_FIELDS",
    "build_request_preview",
    "get_effective_route_binding",
    "get_model_default",
    "get_model_preset",
    "get_route_binding",
    "ensure_model_supports_route",
    "list_model_defaults",
    "list_model_presets",
    "list_route_bindings",
    "model_default_id",
    "model_default_route_references",
    "model_default_setting_key",
    "model_default_write",
    "model_driver_schemas",
    "model_route_compatibility_error",
    "model_preset_write",
    "order_resolved_binding_candidates",
    "preset_route_references",
    "preset_setting_key",
    "resolve_model_default",
    "resolve_model_preset",
    "resolve_route_binding_candidates",
    "route_binding_setting_key",
    "route_binding_write",
    "validate_preset_id",
]
