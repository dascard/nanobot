"""版本化新闻来源描述符与冻结 Registry。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit

from core.registry import RegistryBuilder, RegistrySnapshot


logger = logging.getLogger("nanobot.news.sources")


NEWS_SOURCE_RESOURCE = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "news"
    / "news_sources.v1.json"
)
NEWS_SOURCE_ADAPTER_KINDS = frozenset({
    "rss",
    "juya_rss",
    "html_list",
    "anthropic_html",
    "mistral_html",
    "deepseek_html",
    "qwen_api_json",
    "kimi_html",
    "xai_html",
    "cohere_html",
    "meta_html",
})
NEWS_SOURCE_LIFECYCLES = frozenset({
    "active",
    "preview",
    "deprecated",
    "retired",
})
NEWS_SOURCE_MODES = frozenset({"search", "fast", "quality", "daily"})
NEWS_SOURCE_OVERRIDE_FIELDS = frozenset({
    "enabled",
    "quality_weight",
    "fetch_timeout_seconds",
    "per_run_limit",
})
_RESOURCE_FIELDS = frozenset({
    "source_id",
    "url",
    "adapter_kind",
    "enabled",
    "trust_weight",
    "quality_weight",
    "freshness_policy",
    "fetch_timeout_seconds",
    "per_run_limit",
    "domain",
    "lifecycle",
    "group",
    "modes",
    "category_hints",
    "top_story_eligible",
})


class NewsSourceRegistryError(ValueError):
    """来源资源或 operator override 不满足安全约束。"""


def _required_text(
    value: object,
    *,
    field_name: str,
    max_chars: int = 256,
) -> str:
    text = str(value or "").strip()
    if (
        not text
        or len(text) > max_chars
        or any(ord(character) < 32 for character in text)
    ):
        raise NewsSourceRegistryError(f"{field_name} 无效")
    return text


def _bounded_float(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise NewsSourceRegistryError(f"{field_name} 必须是数值")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise NewsSourceRegistryError(
            f"{field_name} 必须位于 [{minimum}, {maximum}]"
        )
    return normalized


def _bounded_int(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise NewsSourceRegistryError(f"{field_name} 必须是整数")
    if not minimum <= value <= maximum:
        raise NewsSourceRegistryError(
            f"{field_name} 必须位于 [{minimum}, {maximum}]"
        )
    return value


@dataclass(frozen=True, slots=True)
class NewsSourceDescriptor:
    source_id: str
    url: str
    adapter_kind: str
    enabled: bool
    trust_weight: float
    quality_weight: float
    freshness_policy: str
    fetch_timeout_seconds: int
    per_run_limit: int
    domain: str
    lifecycle: str
    group: str
    modes: tuple[str, ...]
    category_hints: tuple[str, ...]
    top_story_eligible: bool
    operator_overridden_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        source_id = _required_text(
            self.source_id,
            field_name="source_id",
            max_chars=96,
        )
        if not source_id.replace("_", "").isalnum():
            raise NewsSourceRegistryError(
                f"source_id 只能包含字母、数字和下划线: {source_id}"
            )
        url = _required_text(self.url, field_name=f"{source_id}.url")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise NewsSourceRegistryError(
                f"{source_id}.url 只允许带主机名的 https URL"
            )
        if parsed.username or parsed.password:
            raise NewsSourceRegistryError(
                f"{source_id}.url 不允许内嵌凭据"
            )
        domain = _required_text(
            self.domain,
            field_name=f"{source_id}.domain",
            max_chars=253,
        ).lower()
        if parsed.hostname.lower() != domain:
            raise NewsSourceRegistryError(
                f"{source_id}.url host 与 domain 不一致"
            )
        adapter_kind = _required_text(
            self.adapter_kind,
            field_name=f"{source_id}.adapter_kind",
            max_chars=64,
        )
        if adapter_kind not in NEWS_SOURCE_ADAPTER_KINDS:
            raise NewsSourceRegistryError(
                f"{source_id}.adapter_kind 未在 allowlist"
            )
        lifecycle = _required_text(
            self.lifecycle,
            field_name=f"{source_id}.lifecycle",
            max_chars=32,
        )
        if lifecycle not in NEWS_SOURCE_LIFECYCLES:
            raise NewsSourceRegistryError(
                f"{source_id}.lifecycle 不受支持"
            )
        if lifecycle == "retired" and self.enabled:
            raise NewsSourceRegistryError(
                f"retired 来源 {source_id} 不能启用"
            )
        modes = tuple(
            _required_text(
                mode,
                field_name=f"{source_id}.modes",
                max_chars=32,
            )
            for mode in self.modes
        )
        if not modes or len(modes) != len(set(modes)):
            raise NewsSourceRegistryError(
                f"{source_id}.modes 不能为空或重复"
            )
        unknown_modes = set(modes) - NEWS_SOURCE_MODES
        if unknown_modes:
            raise NewsSourceRegistryError(
                f"{source_id}.modes 含未知值: {sorted(unknown_modes)}"
            )
        category_hints = tuple(
            _required_text(
                hint,
                field_name=f"{source_id}.category_hints",
                max_chars=48,
            )
            for hint in self.category_hints
        )
        if len(category_hints) != len(set(category_hints)):
            raise NewsSourceRegistryError(
                f"{source_id}.category_hints 不能重复"
            )
        override_fields = tuple(sorted(self.operator_overridden_fields))
        if set(override_fields) - NEWS_SOURCE_OVERRIDE_FIELDS:
            raise NewsSourceRegistryError(
                f"{source_id} 含不允许的 override 字段"
            )
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "adapter_kind", adapter_kind)
        object.__setattr__(self, "lifecycle", lifecycle)
        object.__setattr__(self, "modes", modes)
        object.__setattr__(self, "category_hints", category_hints)
        object.__setattr__(
            self,
            "trust_weight",
            _bounded_float(
                self.trust_weight,
                field_name=f"{source_id}.trust_weight",
                minimum=0,
                maximum=1,
            ),
        )
        object.__setattr__(
            self,
            "quality_weight",
            _bounded_float(
                self.quality_weight,
                field_name=f"{source_id}.quality_weight",
                minimum=0,
                maximum=2,
            ),
        )
        object.__setattr__(
            self,
            "fetch_timeout_seconds",
            _bounded_int(
                self.fetch_timeout_seconds,
                field_name=f"{source_id}.fetch_timeout_seconds",
                minimum=1,
                maximum=30,
            ),
        )
        object.__setattr__(
            self,
            "per_run_limit",
            _bounded_int(
                self.per_run_limit,
                field_name=f"{source_id}.per_run_limit",
                minimum=1,
                maximum=50,
            ),
        )
        object.__setattr__(
            self,
            "freshness_policy",
            _required_text(
                self.freshness_policy,
                field_name=f"{source_id}.freshness_policy",
                max_chars=64,
            ),
        )
        object.__setattr__(
            self,
            "group",
            _required_text(
                self.group,
                field_name=f"{source_id}.group",
                max_chars=64,
            ),
        )
        object.__setattr__(
            self,
            "operator_overridden_fields",
            override_fields,
        )

    @property
    def registry_namespace(self) -> str:
        return "news_source"

    @property
    def registry_id(self) -> str:
        return self.source_id

    @property
    def registry_dependencies(self) -> tuple[str, ...]:
        return ()

    def registry_payload(self) -> Mapping[str, object]:
        return {
            "source_id": self.source_id,
            "url": self.url,
            "adapter_kind": self.adapter_kind,
            "enabled": self.enabled,
            "trust_weight": self.trust_weight,
            "quality_weight": self.quality_weight,
            "freshness_policy": self.freshness_policy,
            "fetch_timeout_seconds": self.fetch_timeout_seconds,
            "per_run_limit": self.per_run_limit,
            "domain": self.domain,
            "lifecycle": self.lifecycle,
            "group": self.group,
            "modes": list(self.modes),
            "category_hints": list(self.category_hints),
            "top_story_eligible": self.top_story_eligible,
            "operator_overridden_fields": list(
                self.operator_overridden_fields
            ),
        }


def _descriptor_from_mapping(
    value: Mapping[str, object],
) -> NewsSourceDescriptor:
    unknown = set(value) - _RESOURCE_FIELDS
    missing = _RESOURCE_FIELDS - set(value)
    if unknown or missing:
        raise NewsSourceRegistryError(
            "来源字段不匹配: "
            f"missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    for boolean_field in ("enabled", "top_story_eligible"):
        if not isinstance(value[boolean_field], bool):
            raise NewsSourceRegistryError(
                f"{value.get('source_id', '<unknown>')}.{boolean_field} "
                "必须是布尔值"
            )
    modes = value["modes"]
    hints = value["category_hints"]
    if not isinstance(modes, list) or not isinstance(hints, list):
        raise NewsSourceRegistryError("modes/category_hints 必须是数组")
    return NewsSourceDescriptor(
        source_id=value["source_id"],
        url=value["url"],
        adapter_kind=value["adapter_kind"],
        enabled=value["enabled"],
        trust_weight=value["trust_weight"],
        quality_weight=value["quality_weight"],
        freshness_policy=value["freshness_policy"],
        fetch_timeout_seconds=value["fetch_timeout_seconds"],
        per_run_limit=value["per_run_limit"],
        domain=value["domain"],
        lifecycle=value["lifecycle"],
        group=value["group"],
        modes=tuple(modes),
        category_hints=tuple(hints),
        top_story_eligible=value["top_story_eligible"],
    )


def _apply_operator_override(
    descriptor: NewsSourceDescriptor,
    override: Mapping[str, object],
) -> NewsSourceDescriptor:
    unknown = set(override) - NEWS_SOURCE_OVERRIDE_FIELDS
    if unknown:
        raise NewsSourceRegistryError(
            f"{descriptor.source_id} override 不允许字段: {sorted(unknown)}"
        )
    if descriptor.lifecycle == "retired" and override.get("enabled"):
        raise NewsSourceRegistryError(
            f"retired 来源 {descriptor.source_id} 不能重新启用"
        )
    if "enabled" in override and not isinstance(override["enabled"], bool):
        raise NewsSourceRegistryError(
            f"{descriptor.source_id}.enabled override 必须是布尔值"
        )
    updates: dict[str, object] = {}
    if "enabled" in override:
        updates["enabled"] = override["enabled"]
    if "quality_weight" in override:
        updates["quality_weight"] = _bounded_float(
            override["quality_weight"],
            field_name=f"{descriptor.source_id}.quality_weight override",
            minimum=0,
            maximum=2,
        )
    if "fetch_timeout_seconds" in override:
        updates["fetch_timeout_seconds"] = _bounded_int(
            override["fetch_timeout_seconds"],
            field_name=(
                f"{descriptor.source_id}.fetch_timeout_seconds override"
            ),
            minimum=1,
            maximum=30,
        )
    if "per_run_limit" in override:
        updates["per_run_limit"] = _bounded_int(
            override["per_run_limit"],
            field_name=f"{descriptor.source_id}.per_run_limit override",
            minimum=1,
            maximum=50,
        )
    updates["operator_overridden_fields"] = tuple(sorted(updates))
    return replace(descriptor, **updates)


class NewsSourceRegistry:
    """构建后冻结，Runtime 只读取同一个 snapshot。"""

    def __init__(
        self,
        descriptors: Iterable[NewsSourceDescriptor],
        *,
        resource_version: str,
    ) -> None:
        self.resource_version = _required_text(
            resource_version,
            field_name="resource_version",
            max_chars=64,
        )
        builder = RegistryBuilder[NewsSourceDescriptor]("news_source")
        for descriptor in descriptors:
            builder.register(descriptor)
        self._snapshot = builder.freeze()

    @property
    def registry_snapshot(
        self,
    ) -> RegistrySnapshot[NewsSourceDescriptor]:
        return self._snapshot

    def get(self, source_id: str) -> NewsSourceDescriptor | None:
        return self._snapshot.get(str(source_id or "").strip())

    def require(self, source_id: str) -> NewsSourceDescriptor:
        return self._snapshot.require(str(source_id or "").strip())

    def select(self, mode: str) -> tuple[NewsSourceDescriptor, ...]:
        normalized = str(mode or "").strip()
        if normalized not in NEWS_SOURCE_MODES:
            raise NewsSourceRegistryError(f"未知来源模式: {normalized}")
        return tuple(
            descriptor
            for descriptor in self._snapshot
            if descriptor.enabled
            and descriptor.lifecycle != "retired"
            and normalized in descriptor.modes
        )

    def descriptors(self) -> tuple[NewsSourceDescriptor, ...]:
        return tuple(self._snapshot)

    def metadata(self) -> Mapping[str, object]:
        return MappingProxyType({
            "resource_version": self.resource_version,
            "generation": self._snapshot.generation,
            "sha256": self._snapshot.sha256,
            "source_count": len(self._snapshot),
        })


def load_news_source_registry(
    resource_path: Path = NEWS_SOURCE_RESOURCE,
    *,
    operator_overrides: Mapping[str, Mapping[str, object]] | None = None,
) -> NewsSourceRegistry:
    """读取 canonical 资源并在完整校验后发布冻结快照。"""

    try:
        raw = json.loads(resource_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NewsSourceRegistryError("新闻来源资源不可读取") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "resource_version",
        "sources",
    }:
        raise NewsSourceRegistryError("新闻来源资源顶层 Schema 不匹配")
    if raw["schema_version"] != 1 or not isinstance(raw["sources"], list):
        raise NewsSourceRegistryError("新闻来源资源版本或 sources 无效")
    descriptors = []
    override_map = dict(operator_overrides or {})
    for item in raw["sources"]:
        if not isinstance(item, dict):
            raise NewsSourceRegistryError("新闻来源条目必须是对象")
        descriptor = _descriptor_from_mapping(item)
        override = override_map.pop(descriptor.source_id, None)
        if override is not None:
            if not isinstance(override, Mapping):
                raise NewsSourceRegistryError(
                    f"{descriptor.source_id} override 必须是对象"
                )
            descriptor = _apply_operator_override(descriptor, override)
        descriptors.append(descriptor)
    if override_map:
        raise NewsSourceRegistryError(
            f"override 引用了未知来源: {sorted(override_map)}"
        )
    return NewsSourceRegistry(
        descriptors,
        resource_version=raw["resource_version"],
    )


NEWS_SOURCE_REGISTRY = load_news_source_registry()
_RUNTIME_SOURCE_REGISTRY_RAW = ""
_RUNTIME_SOURCE_REGISTRY = NEWS_SOURCE_REGISTRY


def get_news_source_registry() -> NewsSourceRegistry:
    return NEWS_SOURCE_REGISTRY


def get_runtime_news_source_registry() -> NewsSourceRegistry:
    """解析 operator override 后返回唯一 Runtime snapshot。"""

    global _RUNTIME_SOURCE_REGISTRY_RAW, _RUNTIME_SOURCE_REGISTRY
    from core.settings_service import settings

    raw = settings.get_str("news.source_overrides", "{}").strip() or "{}"
    if raw == _RUNTIME_SOURCE_REGISTRY_RAW:
        return _RUNTIME_SOURCE_REGISTRY
    try:
        overrides = json.loads(raw)
        if not isinstance(overrides, dict):
            raise NewsSourceRegistryError("operator override 必须是对象")
        candidate = load_news_source_registry(
            operator_overrides=overrides,
        )
    except (json.JSONDecodeError, NewsSourceRegistryError):
        logger.error(
            "新闻来源 override 无效，已拒绝并使用 canonical snapshot"
        )
        candidate = NEWS_SOURCE_REGISTRY
    _RUNTIME_SOURCE_REGISTRY_RAW = raw
    _RUNTIME_SOURCE_REGISTRY = candidate
    return candidate


__all__ = [
    "NEWS_SOURCE_ADAPTER_KINDS",
    "NEWS_SOURCE_OVERRIDE_FIELDS",
    "NEWS_SOURCE_REGISTRY",
    "NEWS_SOURCE_RESOURCE",
    "NewsSourceDescriptor",
    "NewsSourceRegistry",
    "NewsSourceRegistryError",
    "get_news_source_registry",
    "get_runtime_news_source_registry",
    "load_news_source_registry",
]
