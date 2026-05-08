"""热重载配置服务——DB覆盖>环境变量>默认值, TTL缓存+invalidate。"""

import json
import os
import time
from threading import RLock

from core.database import SessionLocal, SystemSetting
from core.config_registry import SETTING_DEFS, SettingDef


class SettingsService:
    def __init__(self, ttl_seconds: float = 2.0):
        self._cache: dict[str, object] = {}
        self._loaded_at = 0.0
        self._version = 0
        self._ttl = ttl_seconds
        self._lock = RLock()

    @property
    def version(self) -> int:
        return self._version

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()
            self._loaded_at = 0.0
            self._version += 1

    def _load_all(self) -> dict[str, object]:
        now = time.time()
        with self._lock:
            if self._cache and now - self._loaded_at < self._ttl:
                return self._cache
            values: dict[str, object] = {}
            try:
                db = SessionLocal()
                try:
                    rows = db.query(SystemSetting).all()
                    row_map = {r.key: r for r in rows if r.value is not None}
                finally:
                    db.close()
                for key, defn in SETTING_DEFS.items():
                    raw = row_map[key].value if key in row_map else os.environ.get(defn.env_name)
                    values[key] = self._cast(raw, defn)
            except Exception:
                for key, defn in SETTING_DEFS.items():
                    if key not in values:
                        values[key] = self._cast(os.environ.get(defn.env_name), defn)
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
        return dict(self._load_all())

    def get(self, key: str, default=None):
        return self._load_all().get(key, default)

    def get_bool(self, key: str, default=False) -> bool:
        return bool(self.get(key, default))

    def get_int(self, key: str, default=0) -> int:
        return int(self.get(key, default))

    def get_float(self, key: str, default=0.0) -> float:
        return float(self.get(key, default))

    def get_str(self, key: str, default="") -> str:
        return str(self.get(key, default))


settings = SettingsService()
