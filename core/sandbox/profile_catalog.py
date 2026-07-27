"""Sandbox 执行 Profile 的版本化单一事实源。"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping


_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")
_PROFILE_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_POLICY_ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_GENERATION_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
_DOMAIN_RE = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)
_ROOT_KEYS = frozenset({"catalog_version", "catalog_generation", "profiles"})
_PROFILE_KEYS = frozenset({
    "profile_id",
    "execution_mode",
    "grantable",
    "image_reference",
    "image_allowlist",
    "network_policy_id",
    "network_allowlist",
    "network_denied_cidrs",
    "network_destination_ports",
    "network_connect_ports",
    "network_proxy_image_reference",
    "network_proxy_image_allowlist",
    "network_proxy_port",
    "shell_path",
    "idle_ttl_seconds",
    "max_ttl_seconds",
    "default_timeout_seconds",
    "max_timeout_seconds",
    "max_processes",
    "global_concurrency",
    "cpu_nano",
    "memory_bytes",
    "pids_limit",
    "tmpfs_bytes",
    "workspace_quota_ceiling_bytes",
    "runtime_quota_bytes",
    "allow_stdin",
    "allow_long_running_processes",
    "allow_detached_processes",
    "apparmor_profile",
})

DEVELOPER_NETWORK_POLICY_ID = "developer_allowlist_v1"
DEVELOPER_REQUIRED_DOMAINS = frozenset({
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "codeload.github.com",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
})
DEVELOPER_REQUIRED_DENIED_CIDRS = frozenset({
    "10.0.0.0/8",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "::1/128",
    "fc00::/7",
    "fe80::/10",
})

DEFAULT_PROFILE_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "sandbox-execution-profiles.v1.json"
)


class ProfileCatalogError(ValueError):
    """Profile manifest 不满足严格契约。"""


def _exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    *,
    context: str,
) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ProfileCatalogError(
            f"{context} 字段不符合契约：missing={missing}, unknown={unknown}"
        )


def _string(
    value: object,
    *,
    name: str,
    pattern: re.Pattern[str] | None = None,
    maximum: int = 255,
) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ProfileCatalogError(f"{name} 无效")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise ProfileCatalogError(f"{name} 无效")
    return value


def _integer(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileCatalogError(f"{name} 必须是整数")
    if not minimum <= value <= maximum:
        raise ProfileCatalogError(f"{name} 超出允许范围")
    return value


def _boolean(value: object, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise ProfileCatalogError(f"{name} 必须是布尔值")
    return value


def _optional_string(
    value: object,
    *,
    name: str,
    maximum: int = 255,
) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise ProfileCatalogError(f"{name} 无效")
    return value


def _string_list(
    value: object,
    *,
    name: str,
    pattern: re.Pattern[str] | None = None,
    maximum_items: int = 256,
    maximum_length: int = 255,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ProfileCatalogError(f"{name} 必须是有界数组")
    result = tuple(
        _string(
            item,
            name=name,
            pattern=pattern,
            maximum=maximum_length,
        )
        for item in value
    )
    if len(result) != len(set(result)):
        raise ProfileCatalogError(f"{name} 存在重复项")
    return result


def _integer_list(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
    maximum_items: int = 32,
) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ProfileCatalogError(f"{name} 必须是有界数组")
    result = tuple(
        _integer(
            item,
            name=name,
            minimum=minimum,
            maximum=maximum,
        )
        for item in value
    )
    if len(result) != len(set(result)):
        raise ProfileCatalogError(f"{name} 存在重复项")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileCatalogError(f"Profile manifest 存在重复字段：{key}")
        result[key] = value
    return result


def _canonical_cidr(
    network: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> str:
    """生成不受 Python IPv4-mapped IPv6 渲染差异影响的规范 CIDR。"""
    if isinstance(network, ipaddress.IPv4Network):
        address = ".".join(str(octet) for octet in network.network_address.packed)
        return f"{address}/{network.prefixlen}"

    packed = network.network_address.packed.hex()
    groups = [
        format(int(packed[index : index + 4], 16), "x")
        for index in range(0, len(packed), 4)
    ]
    best_start = -1
    best_length = 0
    index = 0
    while index < len(groups):
        if groups[index] != "0":
            index += 1
            continue
        end = index
        while end < len(groups) and groups[end] == "0":
            end += 1
        length = end - index
        if length > best_length:
            best_start = index
            best_length = length
        index = end

    if best_length < 2:
        address = ":".join(groups)
    else:
        left = ":".join(groups[:best_start])
        right = ":".join(groups[best_start + best_length :])
        if left and right:
            address = f"{left}::{right}"
        elif left:
            address = f"{left}::"
        elif right:
            address = f"::{right}"
        else:
            address = "::"
    return f"{address}/{network.prefixlen}"


@dataclass(frozen=True, slots=True)
class ExecutionProfileDefinition:
    profile_id: str
    execution_mode: Literal["oneshot", "lease"]
    grantable: bool
    image_reference: str
    image_allowlist: tuple[str, ...]
    network_policy_id: str
    network_allowlist: tuple[str, ...]
    network_denied_cidrs: tuple[str, ...]
    network_destination_ports: tuple[int, ...]
    network_connect_ports: tuple[int, ...]
    network_proxy_image_reference: str
    network_proxy_image_allowlist: tuple[str, ...]
    network_proxy_port: int
    shell_path: str
    idle_ttl_seconds: int
    max_ttl_seconds: int
    default_timeout_seconds: int
    max_timeout_seconds: int
    max_processes: int
    global_concurrency: int
    cpu_nano: int
    memory_bytes: int
    pids_limit: int
    tmpfs_bytes: int
    workspace_quota_ceiling_bytes: int
    runtime_quota_bytes: int
    allow_stdin: bool
    allow_long_running_processes: bool
    allow_detached_processes: bool
    apparmor_profile: str

    @property
    def image_configured(self) -> bool:
        return bool(self.image_allowlist)

    def normalized(self) -> dict[str, Any]:
        value = asdict(self)
        for field_name in (
            "image_allowlist",
            "network_allowlist",
            "network_denied_cidrs",
            "network_destination_ports",
            "network_connect_ports",
            "network_proxy_image_allowlist",
        ):
            value[field_name] = sorted(getattr(self, field_name))
        return value


@dataclass(frozen=True, slots=True)
class ProfileCatalog:
    catalog_version: int
    catalog_generation: str
    profiles: tuple[ExecutionProfileDefinition, ...]
    policy_sha256: str
    _by_id: Mapping[str, ExecutionProfileDefinition] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        by_id = {profile.profile_id: profile for profile in self.profiles}
        object.__setattr__(self, "_by_id", MappingProxyType(by_id))

    def profile(self, profile_id: str) -> ExecutionProfileDefinition:
        try:
            return self._by_id[profile_id]
        except KeyError as exc:
            raise ProfileCatalogError(f"未知 Sandbox Profile：{profile_id}") from exc

    @property
    def by_id(self) -> Mapping[str, ExecutionProfileDefinition]:
        return self._by_id

    def normalized(self) -> dict[str, Any]:
        return {
            "catalog_version": self.catalog_version,
            "catalog_generation": self.catalog_generation,
            "profiles": [
                profile.normalized()
                for profile in sorted(
                    self.profiles,
                    key=lambda item: item.profile_id,
                )
            ],
        }


def _parse_profile(raw: object) -> ExecutionProfileDefinition:
    if not isinstance(raw, Mapping):
        raise ProfileCatalogError("Profile 必须是对象")
    _exact_keys(raw, _PROFILE_KEYS, context="Profile")

    profile_id = _string(
        raw["profile_id"],
        name="profile_id",
        pattern=_PROFILE_ID_RE,
        maximum=64,
    )
    execution_mode = _string(
        raw["execution_mode"],
        name=f"{profile_id}.execution_mode",
        maximum=16,
    )
    if execution_mode not in {"oneshot", "lease"}:
        raise ProfileCatalogError(f"{profile_id}.execution_mode 无效")
    image_reference = _string(
        raw["image_reference"],
        name=f"{profile_id}.image_reference",
    )
    if image_reference.endswith(":latest"):
        raise ProfileCatalogError(f"{profile_id}.image_reference 禁止 latest")
    raw_allowlist = raw["image_allowlist"]
    if not isinstance(raw_allowlist, list):
        raise ProfileCatalogError(f"{profile_id}.image_allowlist 必须是数组")
    image_allowlist = tuple(
        _string(
            item,
            name=f"{profile_id}.image_allowlist",
            pattern=_IMAGE_ID_RE,
            maximum=71,
        )
        for item in raw_allowlist
    )
    if len(image_allowlist) != len(set(image_allowlist)):
        raise ProfileCatalogError(f"{profile_id}.image_allowlist 存在重复项")

    network_policy_id = _string(
        raw["network_policy_id"],
        name=f"{profile_id}.network_policy_id",
        pattern=_POLICY_ID_RE,
        maximum=64,
    )
    network_allowlist = _string_list(
        raw["network_allowlist"],
        name=f"{profile_id}.network_allowlist",
        pattern=_DOMAIN_RE,
        maximum_length=253,
    )
    network_denied_cidrs = _string_list(
        raw["network_denied_cidrs"],
        name=f"{profile_id}.network_denied_cidrs",
        maximum_length=64,
    )
    normalized_cidrs: list[str] = []
    for value in network_denied_cidrs:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            raise ProfileCatalogError(
                f"{profile_id}.network_denied_cidrs 无效"
            ) from exc
        if _canonical_cidr(network) != value:
            raise ProfileCatalogError(
                f"{profile_id}.network_denied_cidrs 必须使用规范 CIDR"
            )
        normalized_cidrs.append(value)
    network_destination_ports = _integer_list(
        raw["network_destination_ports"],
        name=f"{profile_id}.network_destination_ports",
        minimum=1,
        maximum=65535,
    )
    network_connect_ports = _integer_list(
        raw["network_connect_ports"],
        name=f"{profile_id}.network_connect_ports",
        minimum=1,
        maximum=65535,
    )
    network_proxy_image_reference = _optional_string(
        raw["network_proxy_image_reference"],
        name=f"{profile_id}.network_proxy_image_reference",
    )
    network_proxy_image_allowlist = _string_list(
        raw["network_proxy_image_allowlist"],
        name=f"{profile_id}.network_proxy_image_allowlist",
        pattern=_IMAGE_ID_RE,
        maximum_items=1,
        maximum_length=71,
    )
    network_proxy_port = _integer(
        raw["network_proxy_port"],
        name=f"{profile_id}.network_proxy_port",
        minimum=0,
        maximum=65535,
    )

    shell_path = _string(
        raw["shell_path"],
        name=f"{profile_id}.shell_path",
        maximum=128,
    )
    if not shell_path.startswith("/") or ".." in Path(shell_path).parts:
        raise ProfileCatalogError(f"{profile_id}.shell_path 必须是绝对容器路径")
    apparmor_profile = _string(
        raw["apparmor_profile"],
        name=f"{profile_id}.apparmor_profile",
        maximum=128,
    )
    if apparmor_profile == "unconfined":
        raise ProfileCatalogError(f"{profile_id}.apparmor_profile 禁止 unconfined")

    default_timeout_seconds = _integer(
        raw["default_timeout_seconds"],
        name=f"{profile_id}.default_timeout_seconds",
        minimum=1,
        maximum=3600,
    )
    max_timeout_seconds = _integer(
        raw["max_timeout_seconds"],
        name=f"{profile_id}.max_timeout_seconds",
        minimum=1,
        maximum=3600,
    )
    if default_timeout_seconds > max_timeout_seconds:
        raise ProfileCatalogError(f"{profile_id} 默认超时不得超过最大超时")

    idle_ttl_seconds = _integer(
        raw["idle_ttl_seconds"],
        name=f"{profile_id}.idle_ttl_seconds",
        minimum=0,
        maximum=7 * 24 * 3600,
    )
    max_ttl_seconds = _integer(
        raw["max_ttl_seconds"],
        name=f"{profile_id}.max_ttl_seconds",
        minimum=1,
        maximum=7 * 24 * 3600,
    )
    if execution_mode == "lease" and not 1 <= idle_ttl_seconds <= max_ttl_seconds:
        raise ProfileCatalogError(f"{profile_id} Lease TTL 配置无效")
    if execution_mode == "oneshot" and idle_ttl_seconds != 0:
        raise ProfileCatalogError(f"{profile_id} oneshot idle TTL 必须为 0")

    profile = ExecutionProfileDefinition(
        profile_id=profile_id,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        grantable=_boolean(
            raw["grantable"],
            name=f"{profile_id}.grantable",
        ),
        image_reference=image_reference,
        image_allowlist=image_allowlist,
        network_policy_id=network_policy_id,
        network_allowlist=network_allowlist,
        network_denied_cidrs=tuple(normalized_cidrs),
        network_destination_ports=network_destination_ports,
        network_connect_ports=network_connect_ports,
        network_proxy_image_reference=network_proxy_image_reference,
        network_proxy_image_allowlist=network_proxy_image_allowlist,
        network_proxy_port=network_proxy_port,
        shell_path=shell_path,
        idle_ttl_seconds=idle_ttl_seconds,
        max_ttl_seconds=max_ttl_seconds,
        default_timeout_seconds=default_timeout_seconds,
        max_timeout_seconds=max_timeout_seconds,
        max_processes=_integer(
            raw["max_processes"],
            name=f"{profile_id}.max_processes",
            minimum=1,
            maximum=64,
        ),
        global_concurrency=_integer(
            raw["global_concurrency"],
            name=f"{profile_id}.global_concurrency",
            minimum=1,
            maximum=64,
        ),
        cpu_nano=_integer(
            raw["cpu_nano"],
            name=f"{profile_id}.cpu_nano",
            minimum=10_000_000,
            maximum=64_000_000_000,
        ),
        memory_bytes=_integer(
            raw["memory_bytes"],
            name=f"{profile_id}.memory_bytes",
            minimum=64 * 1024 * 1024,
            maximum=256 * 1024 * 1024 * 1024,
        ),
        pids_limit=_integer(
            raw["pids_limit"],
            name=f"{profile_id}.pids_limit",
            minimum=16,
            maximum=4096,
        ),
        tmpfs_bytes=_integer(
            raw["tmpfs_bytes"],
            name=f"{profile_id}.tmpfs_bytes",
            minimum=16 * 1024 * 1024,
            maximum=16 * 1024 * 1024 * 1024,
        ),
        workspace_quota_ceiling_bytes=_integer(
            raw["workspace_quota_ceiling_bytes"],
            name=f"{profile_id}.workspace_quota_ceiling_bytes",
            minimum=1024 * 1024,
            maximum=1024 * 1024 * 1024 * 1024,
        ),
        runtime_quota_bytes=_integer(
            raw["runtime_quota_bytes"],
            name=f"{profile_id}.runtime_quota_bytes",
            minimum=1024 * 1024,
            maximum=1024 * 1024 * 1024 * 1024,
        ),
        allow_stdin=_boolean(
            raw["allow_stdin"],
            name=f"{profile_id}.allow_stdin",
        ),
        allow_long_running_processes=_boolean(
            raw["allow_long_running_processes"],
            name=f"{profile_id}.allow_long_running_processes",
        ),
        allow_detached_processes=_boolean(
            raw["allow_detached_processes"],
            name=f"{profile_id}.allow_detached_processes",
        ),
        apparmor_profile=apparmor_profile,
    )
    if profile.allow_detached_processes:
        raise ProfileCatalogError("v1 不允许 detached 进程")
    network_details = (
        profile.network_allowlist,
        profile.network_denied_cidrs,
        profile.network_destination_ports,
        profile.network_connect_ports,
        profile.network_proxy_image_reference,
        profile.network_proxy_image_allowlist,
        profile.network_proxy_port,
    )
    if profile.network_policy_id == "none":
        if any(network_details):
            raise ProfileCatalogError(
                f"{profile_id} 无网络 Profile 不得携带代理策略"
            )
    elif profile.network_policy_id == DEVELOPER_NETWORK_POLICY_ID:
        if (
            profile.execution_mode != "lease"
            or not profile.grantable
            or not profile.network_proxy_image_reference
            or profile.network_proxy_image_reference.endswith(":latest")
            or profile.network_proxy_port != 3128
            or not DEVELOPER_REQUIRED_DOMAINS.issubset(
                profile.network_allowlist
            )
            or not DEVELOPER_REQUIRED_DENIED_CIDRS.issubset(
                profile.network_denied_cidrs
            )
            or set(profile.network_destination_ports) != {80, 443}
            or set(profile.network_connect_ports) != {443}
        ):
            raise ProfileCatalogError(
                f"{profile_id} developer 网络出口策略不完整"
            )
    elif profile.grantable:
        raise ProfileCatalogError(
            f"{profile_id} 使用了未知的可授权网络策略"
        )
    elif any(network_details):
        raise ProfileCatalogError(
            f"{profile_id} 未就绪 Profile 不得携带代理策略"
        )
    return profile


def parse_profile_catalog(raw: object) -> ProfileCatalog:
    if not isinstance(raw, Mapping):
        raise ProfileCatalogError("Profile manifest 根节点必须是对象")
    _exact_keys(raw, _ROOT_KEYS, context="Profile manifest")
    version = _integer(
        raw["catalog_version"],
        name="catalog_version",
        minimum=1,
        maximum=1,
    )
    generation = _string(
        raw["catalog_generation"],
        name="catalog_generation",
        pattern=_GENERATION_RE,
        maximum=64,
    )
    raw_profiles = raw["profiles"]
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ProfileCatalogError("profiles 必须是非空数组")
    profiles = tuple(_parse_profile(item) for item in raw_profiles)
    profile_ids = tuple(profile.profile_id for profile in profiles)
    if len(profile_ids) != len(set(profile_ids)):
        raise ProfileCatalogError("Profile manifest 存在重复 profile_id")
    required = {"restricted", "developer", "trusted_developer"}
    if set(profile_ids) != required:
        raise ProfileCatalogError(
            "Profile manifest 必须且只能声明 restricted/developer/trusted_developer"
        )
    trusted = next(
        profile
        for profile in profiles
        if profile.profile_id == "trusted_developer"
    )
    if (
        trusted.execution_mode != "lease"
        or trusted.grantable
        or trusted.network_policy_id != "trusted_not_ready"
    ):
        raise ProfileCatalogError(
            "trusted_developer 必须保持不可授权占位；"
            "未来开放必须另立威胁模型里程碑"
        )

    normalized = {
        "catalog_version": version,
        "catalog_generation": generation,
        "profiles": [
            profile.normalized()
            for profile in sorted(profiles, key=lambda item: item.profile_id)
        ],
    }
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ProfileCatalog(
        catalog_version=version,
        catalog_generation=generation,
        profiles=profiles,
        policy_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def load_profile_catalog(
    path: str | Path = DEFAULT_PROFILE_MANIFEST_PATH,
) -> ProfileCatalog:
    manifest_path = Path(path)
    try:
        metadata = manifest_path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or manifest_path.is_symlink()
            or metadata.st_size > 1024 * 1024
        ):
            raise OSError
        raw_bytes = manifest_path.read_bytes()
        raw = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProfileCatalogError(f"Profile manifest 非法常量：{value}")
            ),
        )
    except ProfileCatalogError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileCatalogError("Profile manifest 无法安全读取") from exc
    return parse_profile_catalog(raw)


__all__ = [
    "DEVELOPER_NETWORK_POLICY_ID",
    "DEVELOPER_REQUIRED_DENIED_CIDRS",
    "DEVELOPER_REQUIRED_DOMAINS",
    "DEFAULT_PROFILE_MANIFEST_PATH",
    "ExecutionProfileDefinition",
    "ProfileCatalog",
    "ProfileCatalogError",
    "load_profile_catalog",
    "parse_profile_catalog",
]
