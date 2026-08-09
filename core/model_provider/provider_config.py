"""模型 Provider 实例控制面。

Provider 的身份、驱动类型和凭据状态在这里统一解析；实际 HTTP/KT 调用仍由
对应 Adapter 负责。本模块只返回脱敏公共视图，原始凭据仅保留在内部实例中。
"""

from __future__ import annotations

import json
import importlib.util
import os
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from core.db import system_setting_repository
from core.model_provider.contracts import (
    ProviderCapability,
    ProviderDescriptor,
    ProviderRequestProtocol,
)
from core.route_metadata import canonical_provider_id, is_deprecated_provider


PROVIDER_SETTING_PREFIX = "model.providers."
PROVIDER_CATALOG_PREFIX = "model.catalog."
PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
DRIVER_TYPES = frozenset({"openai", "anthropic", "codex"})
PROVIDER_FIELDS = frozenset({
    "display_name",
    "driver_type",
    "base_url",
    "api_key",
    "enabled",
    "registry_provider",
    "model_discovery_enabled",
    "provider_name",
    "provider_native_tools",
})


def driver_runtime_status(driver_type: str) -> tuple[bool, str]:
    """返回当前进程能否实例化 KT Driver，以及不可用原因。"""

    normalized = validate_driver_type(driver_type)
    dependency = "anthropic" if normalized == "anthropic" else "openai"
    if importlib.util.find_spec(dependency) is None:
        return False, f"缺少 Python 依赖：{dependency}"
    return True, ""


@dataclass(frozen=True, slots=True)
class BuiltinProviderDefinition:
    id: str
    display_name: str
    driver_type: str = "openai"
    aliases: tuple[str, ...] = ()
    registry_provider: str = ""
    model_discovery_enabled: bool = True
    enabled: bool = True
    provider_name: str = ""
    provider_native_tools: tuple[str, ...] = ()


BUILTIN_PROVIDER_DEFINITIONS = MappingProxyType({
    "newapi": BuiltinProviderDefinition(
        id="newapi",
        display_name="New API 网关",
        aliases=("new-api",),
        registry_provider="new-api",
    ),
    "local_llama": BuiltinProviderDefinition(
        id="local_llama",
        display_name="本地 llama.cpp",
        aliases=("local_qwen",),
    ),
    "local_vision": BuiltinProviderDefinition(
        id="local_vision",
        display_name="本地视觉模型",
        aliases=("vision_qwen",),
    ),
    "anthropic": BuiltinProviderDefinition(
        id="anthropic",
        display_name="Anthropic",
        driver_type="anthropic",
        registry_provider="anthropic",
        model_discovery_enabled=False,
        enabled=False,
        provider_name="anthropic",
    ),
    "codex": BuiltinProviderDefinition(
        id="codex",
        display_name="Codex OAuth",
        driver_type="codex",
        registry_provider="codex",
        model_discovery_enabled=False,
        enabled=False,
        provider_name="codex",
        provider_native_tools=("image_gen",),
    ),
})


def canonical_provider_instance_id(provider_id: str) -> str:
    value = str(provider_id or "").strip()
    for definition in BUILTIN_PROVIDER_DEFINITIONS.values():
        if value == definition.id or value in definition.aliases:
            return definition.id
    return canonical_provider_id(value)


@dataclass(frozen=True, slots=True)
class ProviderInstance:
    id: str
    display_name: str
    driver_type: str
    base_url: str
    api_key: str
    enabled: bool
    registry_provider: str
    builtin: bool
    aliases: tuple[str, ...]
    model_discovery_enabled: bool
    provider_name: str
    provider_native_tools: tuple[str, ...]
    credential_configured: bool
    credential_source: str
    catalog_status: dict[str, Any]

    @property
    def route_completion_supported(self) -> bool:
        """当前 Nanobot 业务 Route 只接入 OpenAI-compatible Adapter。"""

        return self.driver_type == "openai"

    @property
    def agent_runtime_supported(self) -> bool:
        """KT Agent Runtime 已接入三种 KT 原生 Provider。"""

        return self.driver_type in DRIVER_TYPES

    @property
    def model_discovery_supported(self) -> bool:
        return self.driver_type == "openai"

    @property
    def runtime_available(self) -> bool:
        return driver_runtime_status(self.driver_type)[0]

    @property
    def runtime_unavailable_reason(self) -> str:
        return driver_runtime_status(self.driver_type)[1]

    @property
    def credential_mode(self) -> str:
        return "oauth" if self.driver_type == "codex" else "api_key"

    @property
    def descriptor(self) -> ProviderDescriptor:
        return provider_descriptor_for_driver(
            self.driver_type,
            provider_id=self.id,
            display_name=self.display_name,
            built_in=self.builtin,
            aliases=self.aliases,
        )

    def internal_view(self) -> dict[str, Any]:
        """供运行时使用的内部视图；调用方不得序列化到 API。"""

        return {
            "id": self.id,
            "display_name": self.display_name,
            "driver_type": self.driver_type,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "enabled": self.enabled,
            "registry_provider": self.registry_provider or None,
            "builtin": self.builtin,
            "legacy_aliases": list(self.aliases),
            "model_discovery_enabled": self.model_discovery_enabled,
            "provider_name": self.provider_name,
            "provider_native_tools": list(self.provider_native_tools),
            "credential_configured": self.credential_configured,
            "credential_source": self.credential_source,
            "credential_mode": self.credential_mode,
            "kt_driver_available": self.driver_type in DRIVER_TYPES,
            "route_completion_supported": self.route_completion_supported,
            "agent_runtime_supported": self.agent_runtime_supported,
            "runtime_available": self.runtime_available,
            "runtime_unavailable_reason": self.runtime_unavailable_reason,
            "model_discovery_supported": self.model_discovery_supported,
            "catalog": dict(self.catalog_status),
            "descriptor": self.descriptor.metadata(),
        }

    def public_view(self) -> dict[str, Any]:
        """返回可安全展示的 Provider 配置，不包含原始凭据。"""

        return {
            "id": self.id,
            "display_name": self.display_name,
            "driver_type": self.driver_type,
            "base_url": self.base_url,
            "enabled": self.enabled,
            "registry_provider": self.registry_provider or None,
            "builtin": self.builtin,
            "legacy_aliases": list(self.aliases),
            "model_discovery_enabled": self.model_discovery_enabled,
            "provider_name": self.provider_name,
            "provider_native_tools": list(self.provider_native_tools),
            "api_key_configured": bool(self.api_key),
            "credential_configured": self.credential_configured,
            "credential_source": self.credential_source,
            "credential_mode": self.credential_mode,
            "kt_driver_available": self.driver_type in DRIVER_TYPES,
            "route_completion_supported": self.route_completion_supported,
            "agent_runtime_supported": self.agent_runtime_supported,
            "runtime_available": self.runtime_available,
            "runtime_unavailable_reason": self.runtime_unavailable_reason,
            "model_discovery_supported": self.model_discovery_supported,
            "catalog": dict(self.catalog_status),
            "descriptor": self.descriptor.metadata(),
        }


def provider_descriptor_for_driver(
    driver_type: str,
    *,
    provider_id: str,
    display_name: str = "",
    built_in: bool = False,
    aliases: tuple[str, ...] = (),
) -> ProviderDescriptor:
    """从实际 Adapter 合同生成 Provider Descriptor，不读取模型名。"""

    normalized = validate_driver_type(driver_type)
    protocol, path, implementation = {
        "openai": (
            ProviderRequestProtocol.OPENAI_CHAT_COMPLETIONS,
            "/chat/completions",
            "openai_compatible",
        ),
        "anthropic": (
            ProviderRequestProtocol.ANTHROPIC_MESSAGES,
            "/messages",
            "anthropic_native",
        ),
        "codex": (
            ProviderRequestProtocol.OPENAI_RESPONSES,
            "/responses",
            "codex_oauth",
        ),
    }[normalized]
    capabilities = frozenset({
        ProviderCapability.CHAT_COMPLETION,
        ProviderCapability.STREAMING,
        ProviderCapability.TOOL_CALLING,
        ProviderCapability.VISION,
        ProviderCapability.REASONING_CONTENT,
        ProviderCapability.CACHE_USAGE,
    })
    evidence = {
        capability: f"{normalized}_adapter_contract"
        for capability in capabilities
    }
    return ProviderDescriptor(
        id=provider_id,
        display_name=display_name or provider_id,
        capabilities=capabilities,
        aliases=aliases,
        implementation=implementation,
        built_in=built_in,
        request_protocol=protocol,
        request_path=path,
        capability_evidence=evidence,
    )


def validate_provider_id(provider_id: str) -> str:
    value = str(provider_id or "").strip()
    if not PROVIDER_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "Provider ID 必须以小写字母开头，且只能包含小写字母、数字、_、-，"
            "长度不超过 64"
        )
    if is_deprecated_provider(value):
        raise ValueError(f"Provider ID {value} 是兼容别名，不能作为新实例 ID")
    if any(
        value in definition.aliases
        for definition in BUILTIN_PROVIDER_DEFINITIONS.values()
    ):
        raise ValueError(f"Provider ID {value} 已被内置实例作为别名占用")
    return value


def validate_driver_type(driver_type: str) -> str:
    value = str(driver_type or "").strip().lower()
    if value == "codex-oauth":
        value = "codex"
    if value not in DRIVER_TYPES:
        raise ValueError(
            f"不支持的 Provider 驱动: {driver_type}；可选 openai/anthropic/codex"
        )
    return value


def provider_setting_key(provider_id: str, field: str) -> str:
    if field not in PROVIDER_FIELDS:
        raise ValueError(f"不支持的 Provider 字段: {field}")
    return f"{PROVIDER_SETTING_PREFIX}{provider_id}.{field}"


def provider_catalog_key(provider_id: str) -> str:
    return f"{PROVIDER_CATALOG_PREFIX}{provider_id}"


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _as_string_list(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set, frozenset)):
        raw_items = value
    else:
        text = str(value or "").strip()
        if not text:
            return ()
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = [item.strip() for item in text.split(",")]
        raw_items = parsed if isinstance(parsed, list) else []
    return tuple(dict.fromkeys(
        str(item or "").strip()
        for item in raw_items
        if str(item or "").strip()
    ))


def _catalog_status(value: object) -> dict[str, Any]:
    try:
        data = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    models = data.get("models")
    if not isinstance(models, list):
        models = []
    last_refresh_ok = data.get("last_refresh_ok")
    return {
        "model_count": len(models),
        "updated_at": str(data.get("updated_at") or ""),
        "last_refresh_ok": (
            bool(last_refresh_ok) if last_refresh_ok is not None else None
        ),
        "last_error": str(data.get("last_error") or ""),
        "stale": last_refresh_ok is False,
    }


def _load_rows(db: Any | None) -> tuple[dict[str, Any], Any | None]:
    if db is not None:
        rows = system_setting_repository(db).list_all()
        return {row.key: row for row in rows}, None

    # 兼容仍直接调用 classifier_client 的运行时入口；API 端始终传入请求 Session。
    from core.database import SessionLocal

    owned_db = SessionLocal()
    try:
        rows = system_setting_repository(owned_db).list_all()
        return {row.key: row for row in rows}, owned_db
    except BaseException:
        owned_db.close()
        raise


def _setting_fallback(key: str, default: object = "") -> object:
    from core.settings_service import settings

    return settings.get(key, default)


def _setting_value_source(
    key: str,
    value: object,
    *,
    configured_source: str,
    default_source: str,
) -> str:
    try:
        from core.config_registry import SETTING_DEFS

        definition = SETTING_DEFS[key]
    except (KeyError, AttributeError):
        return configured_source
    if definition.env_name and os.environ.get(definition.env_name):
        return "environment"
    if str(value).strip() != str(definition.default).strip():
        return configured_source
    return default_source


def _resolved_field(
    provider_id: str,
    field: str,
    row_map: dict[str, Any],
    default: object,
    aliases: tuple[str, ...],
) -> tuple[object, str]:
    key = provider_setting_key(provider_id, field)
    row = row_map.get(key)
    if row is not None:
        return row.value, "database"

    for alias in aliases:
        alias_key = f"{PROVIDER_SETTING_PREFIX}{alias}.{field}"
        alias_row = row_map.get(alias_key)
        if alias_row is not None:
            return alias_row.value, "legacy_database"

    value = _setting_fallback(key, None)
    if value not in (None, ""):
        return value, _setting_value_source(
            key,
            value,
            configured_source="settings",
            default_source="default",
        )

    for alias in aliases:
        alias_key = f"{PROVIDER_SETTING_PREFIX}{alias}.{field}"
        value = _setting_fallback(alias_key, None)
        if value not in (None, ""):
            return value, _setting_value_source(
                alias_key,
                value,
                configured_source="legacy_settings",
                default_source="legacy_default",
            )
    return default, "default"


def _builtin_transport_fallback(provider_id: str) -> tuple[str, str]:
    try:
        from config import (
            CLASSIFIER_API_URL,
            IMAGE_SUMMARY_API_URL,
            NEW_API_BASE_URL,
            NEW_API_KEY,
        )
    except Exception:
        return "", ""
    if provider_id == "newapi":
        return str(NEW_API_BASE_URL or ""), str(NEW_API_KEY or "")
    if provider_id == "local_llama":
        return str(CLASSIFIER_API_URL or ""), ""
    if provider_id == "local_vision":
        return str(IMAGE_SUMMARY_API_URL or ""), ""
    if provider_id == "anthropic":
        return "https://api.anthropic.com", str(
            os.environ.get("ANTHROPIC_API_KEY") or ""
        )
    return "", ""


def _configured_provider_ids(row_map: dict[str, Any]) -> set[str]:
    ids: set[str] = set(BUILTIN_PROVIDER_DEFINITIONS)
    for key in row_map:
        if not key.startswith(PROVIDER_SETTING_PREFIX):
            continue
        remainder = key.removeprefix(PROVIDER_SETTING_PREFIX)
        provider_id, separator, field = remainder.rpartition(".")
        if not separator or field not in PROVIDER_FIELDS:
            continue
        canonical = canonical_provider_instance_id(provider_id)
        if not canonical or is_deprecated_provider(provider_id):
            continue
        if PROVIDER_ID_PATTERN.fullmatch(canonical):
            ids.add(canonical)
    return ids


def _build_instance(provider_id: str, row_map: dict[str, Any]) -> ProviderInstance:
    definition = BUILTIN_PROVIDER_DEFINITIONS.get(provider_id)
    aliases = definition.aliases if definition else ()
    builtin = definition is not None

    display_name, _ = _resolved_field(
        provider_id,
        "display_name",
        row_map,
        definition.display_name if definition else provider_id,
        aliases,
    )
    driver_type, _ = _resolved_field(
        provider_id,
        "driver_type",
        row_map,
        definition.driver_type if definition else "openai",
        aliases,
    )
    try:
        normalized_driver = validate_driver_type(str(driver_type or "openai"))
    except ValueError:
        normalized_driver = "openai"

    base_url, base_url_source = _resolved_field(
        provider_id, "base_url", row_map, "", aliases
    )
    api_key, key_source = _resolved_field(
        provider_id, "api_key", row_map, "", aliases
    )
    enabled, _ = _resolved_field(
        provider_id,
        "enabled",
        row_map,
        definition.enabled if definition else True,
        aliases,
    )
    registry_provider, _ = _resolved_field(
        provider_id,
        "registry_provider",
        row_map,
        definition.registry_provider if definition else provider_id,
        aliases,
    )
    discovery_enabled, _ = _resolved_field(
        provider_id,
        "model_discovery_enabled",
        row_map,
        definition.model_discovery_enabled if definition else True,
        aliases,
    )
    provider_name, _ = _resolved_field(
        provider_id,
        "provider_name",
        row_map,
        definition.provider_name if definition else provider_id,
        aliases,
    )
    provider_native_tools, _ = _resolved_field(
        provider_id,
        "provider_native_tools",
        row_map,
        json.dumps(
            list(definition.provider_native_tools) if definition else [],
            ensure_ascii=False,
        ),
        aliases,
    )

    fallback_base_url, fallback_api_key = _builtin_transport_fallback(provider_id)
    explicit_sources = {
        "database",
        "environment",
        "legacy_database",
        "legacy_settings",
        "settings",
    }
    effective_base_url = (
        base_url
        if base_url_source in explicit_sources
        else (fallback_base_url or base_url)
    )
    effective_api_key = (
        api_key
        if key_source in explicit_sources
        else (fallback_api_key or api_key)
    )
    base_url_text = str(effective_base_url or "").strip().rstrip("/")
    api_key_text = str(effective_api_key or "")
    if not api_key_text:
        key_source = "none"
    elif key_source not in {
        "database",
        "legacy_database",
        "environment",
    }:
        key_source = "environment_or_config"

    credential_configured = bool(api_key_text)
    credential_source = key_source
    if normalized_driver == "codex":
        from core.model_provider.credential_runtime import (
            resolve_provider_credential_status,
        )

        credential_configured, credential_source = (
            resolve_provider_credential_status(normalized_driver)
        )
        api_key_text = ""

    catalog_row = row_map.get(provider_catalog_key(provider_id))
    catalog = _catalog_status(catalog_row.value if catalog_row is not None else "")
    return ProviderInstance(
        id=provider_id,
        display_name=str(display_name or provider_id).strip()[:100] or provider_id,
        driver_type=normalized_driver,
        base_url=base_url_text,
        api_key=api_key_text,
        enabled=_as_bool(enabled, True),
        registry_provider=str(registry_provider or provider_id).strip() or provider_id,
        builtin=builtin,
        aliases=aliases,
        model_discovery_enabled=_as_bool(discovery_enabled, True),
        provider_name=str(provider_name or provider_id).strip() or provider_id,
        provider_native_tools=_as_string_list(provider_native_tools),
        credential_configured=credential_configured,
        credential_source=credential_source,
        catalog_status=catalog,
    )


def list_provider_instances(db: Any | None = None) -> list[ProviderInstance]:
    row_map, owned_db = _load_rows(db)
    try:
        return [
            _build_instance(provider_id, row_map)
            for provider_id in sorted(
                _configured_provider_ids(row_map),
                key=lambda item: (
                    item not in BUILTIN_PROVIDER_DEFINITIONS,
                    item,
                ),
            )
        ]
    finally:
        if owned_db is not None:
            owned_db.close()


def get_provider_instance(
    provider_id: str,
    db: Any | None = None,
) -> ProviderInstance | None:
    canonical = canonical_provider_instance_id(provider_id)
    if not canonical or not PROVIDER_ID_PATTERN.fullmatch(canonical):
        return None
    row_map, owned_db = _load_rows(db)
    try:
        configured_ids = _configured_provider_ids(row_map)
        provider = _build_instance(canonical, row_map)
        if canonical in configured_ids or provider.base_url:
            return provider
        return None
    finally:
        if owned_db is not None:
            owned_db.close()


def provider_config_keys(provider_id: str, db: Any) -> tuple[str, ...]:
    """返回删除自定义 Provider 时需要清理的配置与目录键。"""

    prefix = f"{PROVIDER_SETTING_PREFIX}{provider_id}."
    keys = [
        row.key
        for row in system_setting_repository(db).list_all()
        if row.key.startswith(prefix)
    ]
    catalog_key = provider_catalog_key(provider_id)
    if system_setting_repository(db).get(catalog_key) is not None:
        keys.append(catalog_key)
    return tuple(dict.fromkeys(keys))


def provider_driver_catalog() -> list[dict[str, Any]]:
    catalog = [
        {
            "id": "openai",
            "label": "OpenAI-compatible",
            "credential_mode": "api_key",
            "kt_driver_available": True,
            "route_completion_supported": True,
            "agent_runtime_supported": True,
            "model_discovery_supported": True,
        },
        {
            "id": "anthropic",
            "label": "Anthropic Messages",
            "credential_mode": "api_key",
            "kt_driver_available": True,
            "route_completion_supported": False,
            "agent_runtime_supported": True,
            "model_discovery_supported": False,
        },
        {
            "id": "codex",
            "label": "Codex OAuth",
            "credential_mode": "oauth",
            "kt_driver_available": True,
            "route_completion_supported": False,
            "agent_runtime_supported": True,
            "model_discovery_supported": False,
        },
    ]
    for item in catalog:
        available, reason = driver_runtime_status(item["id"])
        item["runtime_available"] = available
        item["runtime_unavailable_reason"] = reason
        descriptor = provider_descriptor_for_driver(
            item["id"],
            provider_id=f"driver_{item['id']}",
            display_name=str(item["label"]),
        )
        item["request_protocol"] = descriptor.request_protocol.value
        item["request_path"] = descriptor.request_path
        item["capabilities"] = sorted(
            capability.value for capability in descriptor.capabilities
        )
    return catalog


__all__ = [
    "BUILTIN_PROVIDER_DEFINITIONS",
    "DRIVER_TYPES",
    "PROVIDER_FIELDS",
    "ProviderInstance",
    "canonical_provider_instance_id",
    "driver_runtime_status",
    "get_provider_instance",
    "list_provider_instances",
    "provider_catalog_key",
    "provider_config_keys",
    "provider_driver_catalog",
    "provider_descriptor_for_driver",
    "provider_setting_key",
    "validate_driver_type",
    "validate_provider_id",
]
