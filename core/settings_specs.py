"""类型化设置契约与注册表校验。

本模块只描述设置元数据和纯校验规则，不读取环境变量、数据库或 Web 请求。
具体来源解析由 :mod:`core.settings_service` 负责。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias


ValueType: TypeAlias = Literal["str", "int", "float", "bool"]
SettingSourceName: TypeAlias = Literal[
    "database",
    "environment",
    "legacy_database",
    "legacy_environment",
    "default",
]
Reloadability: TypeAlias = Literal["hot", "boot_only"]
SettingScope: TypeAlias = Literal["global", "provider", "route", "tenant"]
SettingSafetyClass: TypeAlias = Literal[
    "ordinary",
    "sensitive",
    "dangerous",
    "invariant",
]
CrossFieldValidator: TypeAlias = Callable[[Mapping[str, object]], None]


DEFAULT_SOURCE_PRECEDENCE: tuple[SettingSourceName, ...] = (
    "database",
    "environment",
    "legacy_database",
    "legacy_environment",
    "default",
)
_ALLOWED_SOURCES = frozenset(DEFAULT_SOURCE_PRECEDENCE)
_ALLOWED_RELOADABILITY = frozenset({"hot", "boot_only"})
_ALLOWED_SCOPES = frozenset({"global", "provider", "route", "tenant"})
_ALLOWED_SAFETY_CLASSES = frozenset({
    "ordinary",
    "sensitive",
    "dangerous",
    "invariant",
})


class SettingCatalogError(ValueError):
    """设置描述符目录不满足启动期不变量。"""


@dataclass(frozen=True)
class SettingDeprecation:
    """设置弃用信息；删除版本必须明确，避免永久兼容层。"""

    since: str
    remove_after: str
    replacement_key: str = ""

    def __post_init__(self) -> None:
        if not self.since.strip():
            raise SettingCatalogError("弃用设置必须声明 since")
        if not self.remove_after.strip():
            raise SettingCatalogError("弃用设置必须声明 remove_after")


@dataclass(frozen=True)
class SettingSpec:
    """一个设置项的完整、可审计契约。

    前十二个字段保留旧 ``SettingDef`` 构造方式。新增字段全部带兼容默认值，
    因而现有注册项无需一次性重写即可获得明确的来源、生命周期和归属信息。
    """

    key: str
    env_name: str
    default: Any
    value_type: ValueType
    category: str
    description: str = ""
    restart_required: bool = False
    min_value: float | None = None
    max_value: float | None = None
    sensitive: bool = False
    dangerous: bool = False
    source_precedence: tuple[SettingSourceName, ...] = DEFAULT_SOURCE_PRECEDENCE
    reloadability: Reloadability | None = None
    owner_module: str = ""
    scope: SettingScope = "global"
    safety_class: SettingSafetyClass | None = None
    deprecation: SettingDeprecation | None = None
    cross_field_validator: CrossFieldValidator | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        key = self.key.strip()
        if not key or key != self.key:
            raise SettingCatalogError("设置 key 不能为空或包含首尾空白")
        if not self.category.strip():
            raise SettingCatalogError(f"{key} 必须声明 category")
        if self.min_value is not None and self.max_value is not None:
            if self.min_value > self.max_value:
                raise SettingCatalogError(f"{key} 的 min_value 不能大于 max_value")

        precedence = tuple(self.source_precedence)
        if not precedence:
            raise SettingCatalogError(f"{key} 必须声明至少一个配置来源")
        if len(precedence) != len(set(precedence)):
            raise SettingCatalogError(f"{key} 的 source_precedence 不能包含重复来源")
        unknown_sources = set(precedence) - _ALLOWED_SOURCES
        if unknown_sources:
            raise SettingCatalogError(
                f"{key} 包含未知配置来源: {sorted(unknown_sources)}"
            )
        if "default" not in precedence:
            raise SettingCatalogError(f"{key} 的 source_precedence 必须包含 default")
        if precedence[-1] != "default":
            raise SettingCatalogError(f"{key} 的 default 必须是最低优先级来源")

        reloadability = self.reloadability or (
            "boot_only" if self.restart_required else "hot"
        )
        if reloadability not in _ALLOWED_RELOADABILITY:
            raise SettingCatalogError(f"{key} 包含未知 reloadability: {reloadability}")
        if self.restart_required and reloadability != "boot_only":
            raise SettingCatalogError(
                f"{key} 标记 restart_required 时 reloadability 必须是 boot_only"
            )
        object.__setattr__(self, "reloadability", reloadability)

        inferred_scope: SettingScope = self.scope
        if self.scope not in _ALLOWED_SCOPES:
            raise SettingCatalogError(f"{key} 包含未知 scope: {self.scope}")
        if self.scope == "global" and key.startswith("model.providers."):
            inferred_scope = "provider"
        elif self.scope == "global" and key.startswith("model.route."):
            inferred_scope = "route"
        object.__setattr__(self, "scope", inferred_scope)

        if key.startswith(("model.providers.", "model.route.")):
            inferred_owner = "core.model_provider"
        elif key.startswith("web_search."):
            inferred_owner = "core.web_search"
        elif key.startswith("sandbox."):
            inferred_owner = "core.sandbox"
        else:
            inferred_owner = f"core.{self.category}"
        owner_module = self.owner_module.strip() or inferred_owner
        object.__setattr__(self, "owner_module", owner_module)

        safety_class = self.safety_class
        if safety_class is None:
            if self.sensitive:
                safety_class = "sensitive"
            elif self.dangerous:
                safety_class = "dangerous"
            else:
                safety_class = "ordinary"
        if safety_class not in _ALLOWED_SAFETY_CLASSES:
            raise SettingCatalogError(
                f"{key} 包含未知 safety_class: {safety_class}"
            )
        if safety_class == "invariant" and "database" in precedence:
            raise SettingCatalogError(
                f"{key} 是安全不变量，source_precedence 不能包含 database"
            )
        object.__setattr__(self, "safety_class", safety_class)

    @property
    def database_override_allowed(self) -> bool:
        """数据库是否是该设置的合法事实来源。"""

        return "database" in self.source_precedence

    def metadata(self) -> dict[str, object]:
        """返回不包含当前值和密钥的安全目录快照。"""

        result: dict[str, object] = {
            "key": self.key,
            "env_name": self.env_name,
            "value_type": self.value_type,
            "category": self.category,
            "source_precedence": list(self.source_precedence),
            "reloadability": self.reloadability,
            "owner_module": self.owner_module,
            "scope": self.scope,
            "safety_class": self.safety_class,
            "database_override_allowed": self.database_override_allowed,
            "sensitive": self.sensitive,
            "dangerous": self.dangerous,
        }
        if self.deprecation is not None:
            result["deprecation"] = {
                "since": self.deprecation.since,
                "remove_after": self.deprecation.remove_after,
                "replacement_key": self.deprecation.replacement_key,
            }
        return result


def validate_setting_catalog(definitions: Mapping[str, SettingSpec]) -> None:
    """启动期验证描述符目录和默认值的跨字段约束。"""

    if not definitions:
        raise SettingCatalogError("设置目录不能为空")
    defaults: dict[str, object] = {}
    validators: list[CrossFieldValidator] = []
    seen_validators: set[int] = set()
    for catalog_key, spec in definitions.items():
        if catalog_key != spec.key:
            raise SettingCatalogError(
                f"设置目录键 {catalog_key!r} 与描述符 key {spec.key!r} 不一致"
            )
        defaults[catalog_key] = spec.default
        validator = spec.cross_field_validator
        if validator is not None and id(validator) not in seen_validators:
            validators.append(validator)
            seen_validators.add(id(validator))
    for validator in validators:
        validator(defaults)


def validate_setting_values(
    definitions: Mapping[str, SettingSpec],
    values: Mapping[str, object],
) -> None:
    """执行已注册的跨字段校验器。"""

    validators: list[CrossFieldValidator] = []
    seen_validators: set[int] = set()
    for spec in definitions.values():
        validator = spec.cross_field_validator
        if validator is not None and id(validator) not in seen_validators:
            validators.append(validator)
            seen_validators.add(id(validator))
    for validator in validators:
        validator(values)


def resolve_boot_setting_value(
    spec: SettingSpec,
    environ: Mapping[str, str],
) -> object:
    """按 SettingSpec 解析启动期 env/default，禁止隐式读取业务数据库。"""

    if spec.reloadability != "boot_only":
        raise SettingCatalogError(f"{spec.key} 不是 boot_only 设置")
    for source in spec.source_precedence:
        if source == "environment" and spec.env_name in environ:
            return environ[spec.env_name]
        if source == "default":
            return spec.default
        if source in {"database", "legacy_database"}:
            raise SettingCatalogError(
                f"启动期设置 {spec.key} 不允许依赖数据库来源"
            )
    raise SettingCatalogError(f"启动期设置 {spec.key} 没有 env/default 来源")


# 兼容现有 import；新代码应使用 SettingSpec。
SettingDef = SettingSpec
