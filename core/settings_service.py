"""类型化配置解析服务——按 SettingSpec 声明的来源优先级解析。"""

from dataclasses import dataclass
import logging
import math
import os
import time
from threading import RLock
from collections.abc import Mapping

from core.config_registry import LEGACY_SETTING_ALIASES, SETTING_DEFS, SettingDef
from core.database import SessionLocal, SystemSetting
from core.settings_specs import SettingSourceName, validate_setting_values


SettingSource = SettingSourceName

logger = logging.getLogger("nanobot.settings")


@dataclass(frozen=True)
class ResolvedSetting:
    key: str
    value: object
    source: SettingSource
    origin: str = "default"
    precedence_index: int = -1

    def provenance(self) -> dict[str, object]:
        """返回不包含设置值的来源说明。"""

        return {
            "key": self.key,
            "source": self.source,
            "origin": self.origin,
            "precedence_index": self.precedence_index,
        }


def coerce_setting_value(value: object, defn: SettingDef) -> object:
    """按注册表定义转换并校验设置值。

    调用方可自行决定校验失败时是拒绝写入，还是回退默认值。
    """

    if value is None:
        return defn.default
    if defn.value_type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}
    if defn.value_type == "int":
        converted: object = int(value)
    elif defn.value_type == "float":
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError(f"{defn.key} 必须是有限数值")
    else:
        return str(value)

    numeric = float(converted)
    if defn.min_value is not None and numeric < defn.min_value:
        raise ValueError(f"{defn.key} 不能小于 {defn.min_value}")
    if defn.max_value is not None and numeric > defn.max_value:
        raise ValueError(f"{defn.key} 不能大于 {defn.max_value}")
    return converted


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

    def __init__(
        self,
        ttl_seconds: float = 2.0,
        session_factory=None,
        definitions: Mapping[str, SettingDef] | None = None,
    ):
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
        self._definitions = dict(definitions or SETTING_DEFS)

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

    def _resolved(
        self,
        *,
        key: str,
        defn: SettingDef,
        value: object,
        source: SettingSource,
        origin: str,
    ) -> ResolvedSetting:
        return ResolvedSetting(
            key=key,
            value=self._cast(value, defn),
            source=source,
            origin=origin,
            precedence_index=defn.source_precedence.index(source),
        )

    def _resolve_from_sources(
        self,
        key: str,
        defn: SettingDef,
        row_map: Mapping[str, object] | None,
    ) -> ResolvedSetting:
        """严格按描述符顺序解析；不允许的来源即使存在也不会生效。"""

        alias = LEGACY_SETTING_ALIASES.get(key)
        for source in defn.source_precedence:
            if source == "database":
                if row_map is not None and key in row_map:
                    return self._resolved(
                        key=key,
                        defn=defn,
                        value=row_map[key].value,
                        source=source,
                        origin=f"system_settings:{key}",
                    )
            elif source == "environment":
                if defn.env_name and defn.env_name in os.environ:
                    return self._resolved(
                        key=key,
                        defn=defn,
                        value=os.environ[defn.env_name],
                        source=source,
                        origin=f"env:{defn.env_name}",
                    )
            elif source == "legacy_database":
                if alias is not None and row_map is not None and alias.key in row_map:
                    self._warn_legacy_alias(
                        canonical_key=key,
                        legacy_key=alias.key,
                        source=source,
                    )
                    return self._resolved(
                        key=key,
                        defn=defn,
                        value=row_map[alias.key].value,
                        source=source,
                        origin=f"system_settings:{alias.key}",
                    )
            elif source == "legacy_environment":
                if alias is not None and alias.env_name in os.environ:
                    self._warn_legacy_alias(
                        canonical_key=key,
                        legacy_key=alias.env_name,
                        source=source,
                    )
                    return self._resolved(
                        key=key,
                        defn=defn,
                        value=os.environ[alias.env_name],
                        source=source,
                        origin=f"env:{alias.env_name}",
                    )
            elif source == "default":
                return self._resolved(
                    key=key,
                    defn=defn,
                    value=defn.default,
                    source=source,
                    origin="default",
                )
        raise RuntimeError(f"设置 {key} 没有可用来源")

    def _resolve_without_database(self, key: str, defn: SettingDef) -> ResolvedSetting:
        return self._resolve_from_sources(key, defn, None)

    def _resolve_with_database(
        self,
        key: str,
        defn: SettingDef,
        row_map: dict[str, object],
    ) -> ResolvedSetting:
        return self._resolve_from_sources(key, defn, row_map)

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
                for key, defn in self._definitions.items():
                    values[key] = self._resolve_with_database(key, defn, row_map)
                validate_setting_values(
                    self._definitions,
                    {key: item.value for key, item in values.items()},
                )
            except Exception as e:
                logger.warning("DB load failed, using env/default: %s", e)
                values = {
                    key: self._resolve_without_database(key, defn)
                    for key, defn in self._definitions.items()
                }
                validate_setting_values(
                    self._definitions,
                    {key: item.value for key, item in values.items()},
                )
            self._cache = values
            self._loaded_at = now
            return values

    def _cast(self, value: object, defn: SettingDef) -> object:
        try:
            return coerce_setting_value(value, defn)
        except (ValueError, TypeError):
            return defn.default

    def all_values(self) -> dict[str, object]:
        return {key: resolved.value for key, resolved in self._load_all().items()}

    def all_resolved(self) -> dict[str, ResolvedSetting]:
        return dict(self._load_all())

    def catalog(self) -> dict[str, dict[str, object]]:
        """返回启动期已校验的安全元数据目录。"""

        return {key: spec.metadata() for key, spec in self._definitions.items()}

    def explain(self, key: str) -> dict[str, object]:
        """解释一个设置的最终来源和覆盖规则，不返回敏感值。"""

        spec = self._definitions.get(key)
        resolved = self.get_resolved(key)
        result = resolved.provenance()
        if spec is not None:
            result.update(spec.metadata())
        return result

    def environment_provenance(self) -> dict[str, dict[str, object]]:
        """列出当前实际由环境变量提供的设置及其 env 来源。"""

        return {
            key: resolved.provenance()
            for key, resolved in self._load_all().items()
            if resolved.source in {"environment", "legacy_environment"}
        }

    def get_resolved(self, key: str, default=None) -> ResolvedSetting:
        resolved = self._load_all().get(key)
        if resolved is not None:
            return resolved
        return ResolvedSetting(
            key=key,
            value=default,
            source="default",
            origin="caller_default",
        )

    def get_resolved_for_session(
        self,
        db,
        key: str,
        default=None,
    ) -> ResolvedSetting:
        """使用调用方 Session 解析单项设置，不跨越其事务边界。"""

        defn = self._definitions.get(key)
        if defn is None:
            row = db.get(SystemSetting, key)
            if row is not None and row.value is not None:
                return ResolvedSetting(
                    key=key,
                    value=row.value,
                    source="database",
                    origin=f"system_settings:{key}",
                    precedence_index=0,
                )
            return ResolvedSetting(
                key=key,
                value=default,
                source="default",
                origin="caller_default",
            )

        row_map: dict[str, object] = {}
        database_sources = {"database", "legacy_database"}
        if database_sources.intersection(defn.source_precedence):
            keys = {key}
            alias = LEGACY_SETTING_ALIASES.get(key)
            if alias is not None:
                keys.add(alias.key)
            rows = (
                db.query(SystemSetting)
                .filter(SystemSetting.key.in_(keys))
                .all()
            )
            row_map = {row.key: row for row in rows if row.value is not None}
        return self._resolve_with_database(key, defn, row_map)

    def get_for_session(self, db, key: str, default=None):
        """返回调用方事务快照中的设置值。"""

        return self.get_resolved_for_session(db, key, default).value

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
