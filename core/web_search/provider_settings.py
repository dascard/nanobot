"""Web Search provider 配置解析与写入。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from core.database import SystemSetting
from core.settings_service import settings
from core.web_search.provider_catalog import (
    ProviderCatalogItem,
    env_name_for,
    get_provider_catalog,
    list_provider_catalog,
)


@dataclass(frozen=True)
class ProviderResolvedConfig:
    provider_id: str
    enabled: bool
    base_url: str
    api_key: str
    api_key_configured: bool
    api_key_source: str | None
    priority: int = 0

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.provider_id,
            "enabled": self.enabled,
            "base_url": self.base_url,
            "api_key_configured": self.api_key_configured,
            "api_key_source": self.api_key_source,
            "priority": self.priority,
        }


def setting_key(provider_id: str, field: str) -> str:
    return f"web_search.providers.{provider_id}.{field}"


def _row_value(db: Session, provider_id: str, field: str) -> tuple[str | None, str | None]:
    row = db.query(SystemSetting).filter(SystemSetting.key == setting_key(provider_id, field)).first()
    if row is not None and row.value is not None:
        return str(row.value), "db"
    env_value = os.environ.get(env_name_for(provider_id, field))
    if env_value is not None:
        return env_value, "env"
    return None, None


def _bool_value(raw: str | None, default: bool) -> bool:
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _int_value(raw: str | None, default: int) -> int:
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)


def _catalog_default(item: ProviderCatalogItem, field: str) -> Any:
    if field == "enabled":
        return item.enabled_by_default
    if field == "base_url":
        return item.default_base_url
    if field == "priority":
        return item.default_priority
    return ""


def _require_provider(provider_id: str) -> ProviderCatalogItem:
    item = get_provider_catalog(provider_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown web search provider")
    return item


def resolve_provider_config(db: Session, provider_id: str) -> ProviderResolvedConfig:
    item = _require_provider(provider_id)
    enabled_raw, _enabled_source = _row_value(db, provider_id, "enabled")
    base_url_raw, _base_url_source = _row_value(db, provider_id, "base_url")
    api_key_raw, api_key_source = _row_value(db, provider_id, "api_key")
    priority_raw, _priority_source = _row_value(db, provider_id, "priority")

    enabled = _bool_value(enabled_raw, bool(_catalog_default(item, "enabled")))
    base_url = str(base_url_raw if base_url_raw is not None else _catalog_default(item, "base_url") or "")
    api_key = str(api_key_raw or "")
    api_key_configured = bool(api_key_source and api_key)
    priority = _int_value(priority_raw, int(_catalog_default(item, "priority")))

    return ProviderResolvedConfig(
        provider_id=provider_id,
        enabled=enabled,
        base_url=base_url,
        api_key=api_key,
        api_key_configured=api_key_configured,
        api_key_source=api_key_source if api_key_configured else None,
        priority=priority,
    )


def list_provider_configs(db: Session) -> list[ProviderResolvedConfig]:
    return [resolve_provider_config(db, item.id) for item in list_provider_catalog()]


def _upsert_setting(db: Session, key: str, value: str, description: str = "") -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is None:
        db.add(SystemSetting(key=key, value=value, description=description))
    else:
        row.value = value
        if description:
            row.description = description


def _delete_setting(db: Session, key: str) -> None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row is not None:
        db.delete(row)


def _validate_base_url(item: ProviderCatalogItem, value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        return ""
    if not item.supports_base_url:
        raise HTTPException(status_code=422, detail="Provider does not support base_url")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="Invalid base_url")
    return normalized


def _validate_priority(value: Any) -> int:
    try:
        priority = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid priority")
    if priority < 0 or priority > 1_000_000:
        raise HTTPException(status_code=422, detail="Invalid priority")
    return priority


def update_provider_config(db: Session, provider_id: str, payload: dict[str, Any]) -> ProviderResolvedConfig:
    item = _require_provider(provider_id)
    api_key = payload.get("api_key")
    clear_api_key = bool(payload.get("clear_api_key"))
    if clear_api_key and str(api_key or "").strip():
        raise HTTPException(status_code=422, detail="clear_api_key conflicts with api_key")

    if "enabled" in payload and payload.get("enabled") is not None:
        enabled = bool(payload.get("enabled"))
        _upsert_setting(
            db,
            setting_key(provider_id, "enabled"),
            "true" if enabled else "false",
            f"{item.name} 启用状态",
        )

    if "base_url" in payload and payload.get("base_url") is not None:
        base_url = _validate_base_url(item, str(payload.get("base_url") or ""))
        if base_url:
            _upsert_setting(
                db,
                setting_key(provider_id, "base_url"),
                base_url,
                f"{item.name} Base URL",
            )
        else:
            _delete_setting(db, setting_key(provider_id, "base_url"))

    if "priority" in payload and payload.get("priority") is not None:
        priority = _validate_priority(payload.get("priority"))
        _upsert_setting(
            db,
            setting_key(provider_id, "priority"),
            str(priority),
            f"{item.name} 自动搜索优先级",
        )

    if clear_api_key:
        _delete_setting(db, setting_key(provider_id, "api_key"))
    elif api_key is not None and str(api_key).strip():
        _upsert_setting(
            db,
            setting_key(provider_id, "api_key"),
            str(api_key).strip(),
            f"{item.name} API Key",
        )

    db.commit()
    settings.invalidate()
    return resolve_provider_config(db, provider_id)
