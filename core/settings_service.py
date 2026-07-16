"""热重载配置服务——DB覆盖>环境变量>默认值, TTL缓存+invalidate。"""

from dataclasses import dataclass
import logging
import os
import time
from threading import RLock
from typing import Literal

from core.config_registry import LEGACY_SETTING_ALIASES, SETTING_DEFS, SettingDef
from core.database import SessionLocal, SystemSetting


SettingSource = Literal[
    "database",
    "environment",
    "legacy_database",
    "legacy_environment",
    "default",
]

logger = logging.getLogger("nanobot.settings")


@dataclass(frozen=True)
class ResolvedSetting:
    key: str
    value: object
    source: SettingSource


def serialize_resolved_setting(
    defn: SettingDef,
    resolved: ResolvedSetting,
) -> dict[str, object]:
    """生成 GET、PUT 与 reset 共用的设置值响应片段。"""

    if defn.sensitive:
        return {
            "value": None,
            "display_value": "****",
            "configured": bool(str(resolved.value or "").strip()),
            "source": resolved.source,
        }
    return {
        "value": resolved.value,
        "display_value": str(resolved.value),
        "configured": True,
        "source": resolved.source,
    }


class SettingsService:
    def set_session_factory(self, factory):
        self._session_factory = factory
        self.invalidate()

    def __init__(self, ttl_seconds: float = 2.0, session_factory=None):
        if session_factory is not None:
            self._session_factory = session_factory
        else:
            self._session_factory = SessionLocal
        self._cache: dict[str, ResolvedSetting] = {}
        self._loaded_at = 0.0
        self._version = 0
        self._ttl = ttl_seconds
        self._lock = RLock()
        self._warned_legacy_aliases: set[tuple[str, SettingSource]] = set()

    @property
    def version(self) -> int:
        return self._version

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            self._loaded_at = 0.0
            self._version += 1

    def _warn_legacy_alias(
        self,
        *,
        canonical_key: str,
        legacy_key: str,
        source: SettingSource,
    ) -> None:
        warning_key = (canonical_key, source)
        if warning_key in self._warned_legacy_aliases:
            return
        self._warned_legacy_aliases.add(warning_key)
        logger.warning(
            "设置别名已弃用 canonical_key=%s legacy_key=%s source=%s",
            canonical_key,
            legacy_key,
            source,
        )

    def _resolve_without_database(self, key: str, defn: SettingDef) -> ResolvedSetting:
        if defn.env_name and defn.env_name in os.environ:
            return ResolvedSetting(
                key=key,
                value=self._cast(os.environ[defn.env_name], defn),
                source="environment",
            )
        alias = LEGACY_SETTING_ALIASES.get(key)
        if alias is not None and alias.env_name in os.environ:
            self._warn_legacy_alias(
                canonical_key=key,
                legacy_key=alias.env_name,
                source="legacy_environment",
            )
            return ResolvedSetting(
                key=key,
                value=self._cast(os.environ[alias.env_name], defn),
                source="legacy_environment",
            )
        return ResolvedSetting(key=key, value=defn.default, source="default")

    def _resolve_with_database(
        self,
        key: str,
        defn: SettingDef,
        row_map: dict[str, object],
    ) -> ResolvedSetting:
        if key in row_map:
            return ResolvedSetting(
                key=key,
                value=self._cast(row_map[key].value, defn),
                source="database",
            )
        if defn.env_name and defn.env_name in os.environ:
            return ResolvedSetting(
                key=key,
                value=self._cast(os.environ[defn.env_name], defn),
                source="environment",
            )
        alias = LEGACY_SETTING_ALIASES.get(key)
        if alias is not None and alias.key in row_map:
            self._warn_legacy_alias(
                canonical_key=key,
                legacy_key=alias.key,
                source="legacy_database",
            )
            return ResolvedSetting(
                key=key,
                value=self._cast(row_map[alias.key].value, defn),
                source="legacy_database",
            )
        return self._resolve_without_database(key, defn)

    def _load_all(self) -> dict[str, ResolvedSetting]:
        now = time.time()
        with self._lock:
            if self._cache and now - self._loaded_at < self._ttl:
                return self._cache
            values: dict[str, ResolvedSetting] = {}
            try:
                db = self._session_factory()
                try:
                    rows = db.query(SystemSetting).all()
                    row_map = {r.key: r for r in rows if r.value is not None}
                finally:
                    db.close()
                for key, defn in SETTING_DEFS.items():
                    values[key] = self._resolve_with_database(key, defn, row_map)
            except Exception as e:
                logger.warning("DB load failed, using env/default: %s", e)
                for key, defn in SETTING_DEFS.items():
                    if key not in values:
                        values[key] = self._resolve_without_database(key, defn)
            self._cache = values
            self._loaded_at = now
            return values

    def _cast(self, value: object, defn: SettingDef) -> object:
        if value is None:
            return defn.default
        try:
            if defn.value_type == "bool":
                if isinstance(value, bool):
                    return value
                return str(value).lower() in {"1", "true", "yes", "on"}
            if defn.value_type == "int":
                return int(value)
            if defn.value_type == "float":
                return float(value)
            return str(value)
        except (ValueError, TypeError):
            return defn.default

    def all_values(self) -> dict[str, object]:
        return {key: resolved.value for key, resolved in self._load_all().items()}

    def all_resolved(self) -> dict[str, ResolvedSetting]:
        return dict(self._load_all())

    def get_resolved(self, key: str, default=None) -> ResolvedSetting:
        resolved = self._load_all().get(key)
        if resolved is not None:
            return resolved
        return ResolvedSetting(key=key, value=default, source="default")

    def get(self, key: str, default=None):
        resolved = self._load_all().get(key)
        return resolved.value if resolved is not None else default

    def get_bool(self, key: str, default=False) -> bool:
        return bool(self.get(key, default))

    def get_int(self, key: str, default=0) -> int:
        return int(self.get(key, default))

    def get_float(self, key: str, default=0.0) -> float:
        return float(self.get(key, default))

    def get_str(self, key: str, default="") -> str:
        return str(self.get(key, default))


settings = SettingsService()
