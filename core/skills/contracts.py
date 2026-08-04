"""Agent Skills 规范解析、作用域与请求级版本锁合同。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import mimetypes
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping

import yaml


SKILL_MD_MAX_BYTES = 512 * 1024
SKILL_FILE_MAX_BYTES = 2 * 1024 * 1024
SKILL_BUNDLE_MAX_BYTES = 10 * 1024 * 1024
SKILL_BUNDLE_MAX_FILES = 128
SKILL_CATALOG_MAX_BYTES = 4096

_SKILL_NAME_PATTERN = re.compile(
    r"^(?!-)(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*$"
)
_SEMVER_VALUE_PATTERN = (
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_SEMVER_PATTERN = re.compile(rf"^{_SEMVER_VALUE_PATTERN}$")
_DEPENDENCY_PATTERN = re.compile(
    r"(?P<name>(?!-)(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*)@"
    rf"(?P<version>{_SEMVER_VALUE_PATTERN})"
)
_PERMISSION_PATTERN = re.compile(
    r"^[a-z][a-z0-9_.-]{0,63}:[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"
)
_ALLOWED_TOOL_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.-]{0,127}(?:\([^\r\n()]{1,128}\))?$"
)
_TARGET_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$")
_PACKAGE_ID_PATTERN = re.compile(r"^(?:skillpkg|bundled)_[0-9a-f]{32,64}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SPEC_FIELDS = frozenset(
    {
        "name",
        "description",
        "license",
        "compatibility",
        "metadata",
        "allowed-tools",
    }
)


class SkillContractError(ValueError):
    """Skill 包或运行时锁违反稳定合同。"""


class SkillScope(str, Enum):
    BUILTIN = "builtin"
    PROJECT = "project"
    AGENT = "agent"
    USER = "user"


_SCOPE_PRIORITY = MappingProxyType(
    {
        SkillScope.BUILTIN: 100,
        SkillScope.AGENT: 200,
        SkillScope.USER: 300,
        SkillScope.PROJECT: 400,
    }
)


def skill_scope_priority(scope: SkillScope) -> int:
    return _SCOPE_PRIORITY[SkillScope(scope)]


def _required_text(value: object, field: str, max_chars: int) -> str:
    if not isinstance(value, str):
        raise SkillContractError(f"{field} 必须是字符串")
    normalized = value.strip()
    if not normalized:
        raise SkillContractError(f"{field} 不能为空")
    if len(normalized) > max_chars:
        raise SkillContractError(f"{field} 长度不能超过 {max_chars}")
    if any(ord(char) < 32 and char not in {"\t", "\n"} for char in normalized):
        raise SkillContractError(f"{field} 包含控制字符")
    return normalized


def _optional_text(value: object, field: str, max_chars: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_chars:
        raise SkillContractError(f"{field} 长度不能超过 {max_chars}")
    if any(ord(char) < 32 and char not in {"\t", "\n"} for char in normalized):
        raise SkillContractError(f"{field} 包含控制字符")
    return normalized


def normalize_skill_name(value: object) -> str:
    name = _required_text(value, "skill.name", 64)
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillContractError("skill.name 必须是规范 kebab-case")
    return name


def normalize_semver(value: object) -> str:
    version = _required_text(value, "skill.version", 64)
    if not _SEMVER_PATTERN.fullmatch(version):
        raise SkillContractError("skill.version 必须是 SemVer")
    return version


def semver_key(value: str) -> tuple[object, ...]:
    """生成足以进行升级方向判断的 SemVer 排序键。"""

    version = normalize_semver(value)
    core_and_pre = version.split("+", 1)[0]
    core, separator, prerelease = core_and_pre.partition("-")
    major, minor, patch = (int(item) for item in core.split("."))
    if not separator:
        return major, minor, patch, 1, ()
    parts: list[tuple[int, object]] = []
    for item in prerelease.split("."):
        parts.append((0, int(item)) if item.isdigit() else (1, item))
    return major, minor, patch, 0, tuple(parts)


@dataclass(frozen=True, slots=True)
class SkillScopeTarget:
    scope: SkillScope
    scope_key: str

    def __post_init__(self) -> None:
        try:
            scope = SkillScope(self.scope)
        except ValueError as exc:
            raise SkillContractError("skill.scope 无效") from exc
        key = _required_text(self.scope_key, "skill.scope_key", 255)
        if not _TARGET_KEY_PATTERN.fullmatch(key) or "/../" in f"/{key}/":
            raise SkillContractError("skill.scope_key 不是规范标识符")
        if scope is SkillScope.BUILTIN and key != "builtin":
            raise SkillContractError("builtin scope_key 必须是 builtin")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "scope_key", key)


@dataclass(frozen=True, slots=True)
class SkillBundleFile:
    relative_path: str
    content: bytes
    media_type: str = ""

    def __post_init__(self) -> None:
        raw_path = str(self.relative_path or "")
        path = raw_path.strip()
        if (
            not path
            or path != raw_path
            or len(path) > 512
            or "\\" in path
            or "\x00" in path
        ):
            raise SkillContractError("Skill 资源路径必须是 POSIX 相对路径")
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
            raise SkillContractError("Skill 资源路径不能越界")
        normalized = parsed.as_posix()
        if normalized != path:
            raise SkillContractError("Skill 资源路径必须使用规范 POSIX 形式")
        if normalized in {"SKILL.md", ".git"} or normalized.startswith(".git/"):
            raise SkillContractError("Skill 资源路径属于保留位置")
        raw = self.content
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise TypeError("Skill 资源内容必须是 bytes")
        content = bytes(raw)
        if not content:
            raise SkillContractError("Skill 资源文件不能为空")
        if len(content) > SKILL_FILE_MAX_BYTES:
            raise SkillContractError("单个 Skill 资源超过大小上限")
        media_type = str(self.media_type or "").strip().lower()
        if not media_type:
            media_type = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
        if len(media_type) > 128 or "/" not in media_type:
            raise SkillContractError("Skill 资源 media_type 无效")
        object.__setattr__(self, "relative_path", normalized)
        object.__setattr__(self, "content", content)
        object.__setattr__(self, "media_type", media_type)

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True, slots=True)
class ParsedSkillBundle:
    name: str
    version: str
    description: str
    license_text: str
    compatibility: str
    metadata: Mapping[str, str]
    allowed_tools: tuple[str, ...]
    dependencies: tuple[str, ...]
    required_permissions: tuple[str, ...]
    skill_md: bytes
    body: str
    files: tuple[SkillBundleFile, ...]
    skill_md_sha256: str
    bundle_sha256: str
    bundle_size: int

    @property
    def resource_paths(self) -> tuple[str, ...]:
        return tuple(item.relative_path for item in self.files)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise SkillContractError(f"SKILL.md YAML 包含重复字段：{key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _frontmatter(raw: bytes) -> tuple[dict[str, object], str]:
    if not raw or len(raw) > SKILL_MD_MAX_BYTES:
        raise SkillContractError("SKILL.md 为空或超过大小上限")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillContractError("SKILL.md 必须是严格 UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise SkillContractError("SKILL.md 必须以 YAML frontmatter 开始")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise SkillContractError("SKILL.md 缺少 frontmatter 结束标记") from exc
    try:
        parsed = yaml.load("\n".join(lines[1:end]), Loader=_UniqueKeyLoader)
    except SkillContractError:
        raise
    except yaml.YAMLError as exc:
        raise SkillContractError("SKILL.md YAML 无法解析") from exc
    if not isinstance(parsed, dict):
        raise SkillContractError("SKILL.md frontmatter 必须是对象")
    if any(not isinstance(key, str) for key in parsed):
        raise SkillContractError("SKILL.md frontmatter 字段名必须是字符串")
    unknown = sorted(set(parsed) - _SPEC_FIELDS)
    if unknown:
        raise SkillContractError(
            "SKILL.md 未知顶层字段必须移入 metadata：" + ", ".join(unknown)
        )
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise SkillContractError("SKILL.md 正文不能为空")
    return parsed, body


def _metadata(value: object) -> Mapping[str, str]:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, dict):
        raise SkillContractError("skill.metadata 必须是字符串映射")
    if len(value) > 32:
        raise SkillContractError("skill.metadata 字段过多")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _required_text(raw_key, "skill.metadata.key", 128)
        if not isinstance(raw_value, str):
            raise SkillContractError("skill.metadata 的值必须是字符串")
        item = raw_value.strip()
        if len(item) > 2048:
            raise SkillContractError(f"skill.metadata[{key}] 过长")
        normalized[key] = item
    return MappingProxyType(dict(sorted(normalized.items())))


def _csv_items(value: str, *, field: str) -> tuple[str, ...]:
    if not value.strip():
        return ()
    items = tuple(sorted(item.strip() for item in value.split(",") if item.strip()))
    if len(items) != len(set(items)):
        raise SkillContractError(f"{field} 不能重复")
    return items


def _allowed_tools(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, str):
        raise SkillContractError("allowed-tools 必须是空格分隔字符串")
    tools = tuple(sorted(item for item in value.split() if item))
    if len(tools) != len(set(tools)):
        raise SkillContractError("allowed-tools 不能重复")
    if len(tools) > 32 or any(not _ALLOWED_TOOL_PATTERN.fullmatch(item) for item in tools):
        raise SkillContractError("allowed-tools 包含无效工具声明")
    return tools


def parse_skill_bundle(
    skill_md: bytes,
    *,
    files: tuple[SkillBundleFile, ...] = (),
    expected_name: str = "",
) -> ParsedSkillBundle:
    """严格解析一个已上传包；不访问网络、不运行脚本。"""

    raw = bytes(skill_md)
    frontmatter, body = _frontmatter(raw)
    name = normalize_skill_name(frontmatter.get("name"))
    if expected_name and name != normalize_skill_name(expected_name):
        raise SkillContractError("skill.name 必须与父目录或安装目标一致")
    description = _required_text(frontmatter.get("description"), "skill.description", 1024)
    license_text = str(frontmatter.get("license") or "").strip()
    compatibility = str(frontmatter.get("compatibility") or "").strip()
    if len(license_text) > 512:
        raise SkillContractError("skill.license 长度不能超过 512")
    if len(compatibility) > 500:
        raise SkillContractError("skill.compatibility 长度不能超过 500")
    metadata = _metadata(frontmatter.get("metadata"))
    version_values = {
        item
        for item in (metadata.get("version", ""), metadata.get("nanobot.version", ""))
        if item
    }
    if len(version_values) > 1:
        raise SkillContractError("metadata 只能声明一个一致的 version")
    # Agent Skills 标准不强制版本；未扩展版本的兼容包固定为 0.0.0，
    # 后续若要升级必须显式补充 metadata.version。
    version = normalize_semver(next(iter(version_values), "0.0.0"))
    dependencies = _csv_items(
        metadata.get("nanobot.dependencies", ""),
        field="skill.dependencies",
    )
    for dependency in dependencies:
        match = _DEPENDENCY_PATTERN.fullmatch(dependency)
        if match is None:
            raise SkillContractError("skill.dependencies 必须使用 name@SemVer")
        if match.group("name") == name:
            raise SkillContractError("Skill 不能依赖自身")
    permissions = _csv_items(
        metadata.get("nanobot.permissions", ""),
        field="skill.required_permissions",
    )
    if any(not _PERMISSION_PATTERN.fullmatch(item) for item in permissions):
        raise SkillContractError("skill.required_permissions 包含无效声明")
    allowed_tools = _allowed_tools(frontmatter.get("allowed-tools"))
    normalized_files = tuple(sorted(files, key=lambda item: item.relative_path))
    if len(normalized_files) > SKILL_BUNDLE_MAX_FILES:
        raise SkillContractError("Skill 资源文件数量超过上限")
    paths = [item.relative_path for item in normalized_files]
    if len(paths) != len(set(paths)):
        raise SkillContractError("Skill 资源路径不能重复")
    bundle_size = len(raw) + sum(len(item.content) for item in normalized_files)
    if bundle_size > SKILL_BUNDLE_MAX_BYTES:
        raise SkillContractError("Skill 包超过总大小上限")
    skill_md_sha256 = hashlib.sha256(raw).hexdigest()
    manifest = {
        "SKILL.md": {"sha256": skill_md_sha256, "size": len(raw)},
        "files": [
            {
                "path": item.relative_path,
                "sha256": item.content_sha256,
                "size": len(item.content),
                "media_type": item.media_type,
            }
            for item in normalized_files
        ],
    }
    bundle_sha256 = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return ParsedSkillBundle(
        name=name,
        version=version,
        description=description,
        license_text=license_text,
        compatibility=compatibility,
        metadata=metadata,
        allowed_tools=allowed_tools,
        dependencies=dependencies,
        required_permissions=permissions,
        skill_md=raw,
        body=body,
        files=normalized_files,
        skill_md_sha256=skill_md_sha256,
        bundle_sha256=bundle_sha256,
        bundle_size=bundle_size,
    )


@dataclass(frozen=True, slots=True)
class RuntimeSkillLockEntry:
    package_id: str
    scope: SkillScope
    name: str
    version: str
    description: str
    license_text: str
    compatibility: str
    content_sha256: str
    bundle_sha256: str
    allowed_tools: tuple[str, ...]
    dependencies: tuple[str, ...]
    required_permissions: tuple[str, ...]
    source_kind: str

    def __post_init__(self) -> None:
        package_id = str(self.package_id or "").strip()
        if not _PACKAGE_ID_PATTERN.fullmatch(package_id):
            raise SkillContractError("skill lock package_id 无效")
        object.__setattr__(self, "package_id", package_id)
        try:
            scope = SkillScope(self.scope)
        except ValueError as exc:
            raise SkillContractError("skill lock scope 无效") from exc
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "name", normalize_skill_name(self.name))
        object.__setattr__(self, "version", normalize_semver(self.version))
        object.__setattr__(
            self,
            "description",
            _required_text(self.description, "skill.description", 1024),
        )
        license_text = _optional_text(
            self.license_text,
            "skill lock license_text",
            512,
        )
        compatibility = _optional_text(
            self.compatibility,
            "skill lock compatibility",
            500,
        )
        object.__setattr__(self, "license_text", license_text)
        object.__setattr__(self, "compatibility", compatibility)
        for field in ("content_sha256", "bundle_sha256"):
            digest = str(getattr(self, field) or "").strip().lower()
            if not _SHA256_PATTERN.fullmatch(digest):
                raise SkillContractError(f"skill lock {field} 无效")
            object.__setattr__(self, field, digest)
        source_kind = str(self.source_kind or "").strip()
        if source_kind not in {"bundled", "managed"}:
            raise SkillContractError("skill lock source_kind 无效")
        object.__setattr__(self, "source_kind", source_kind)
        for field in ("allowed_tools", "dependencies", "required_permissions"):
            values = tuple(sorted(str(item) for item in getattr(self, field)))
            if len(values) != len(set(values)):
                raise SkillContractError(f"skill lock {field} 不能重复")
            object.__setattr__(self, field, values)
        if any(not _ALLOWED_TOOL_PATTERN.fullmatch(item) for item in self.allowed_tools):
            raise SkillContractError("skill lock allowed_tools 包含无效声明")
        if any(not _DEPENDENCY_PATTERN.fullmatch(item) for item in self.dependencies):
            raise SkillContractError("skill lock dependencies 包含无效声明")
        if any(
            not _PERMISSION_PATTERN.fullmatch(item)
            for item in self.required_permissions
        ):
            raise SkillContractError("skill lock required_permissions 包含无效声明")

    def to_dict(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "scope": self.scope.value,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "license_text": self.license_text,
            "compatibility": self.compatibility,
            "content_sha256": self.content_sha256,
            "bundle_sha256": self.bundle_sha256,
            "allowed_tools": list(self.allowed_tools),
            "dependencies": list(self.dependencies),
            "required_permissions": list(self.required_permissions),
            "source_kind": self.source_kind,
        }


@dataclass(frozen=True, slots=True)
class RuntimeSkillLock:
    entries: tuple[RuntimeSkillLockEntry, ...]
    diagnostics: tuple[str, ...] = ()
    sha256: str = ""

    def __post_init__(self) -> None:
        entries = tuple(sorted(self.entries, key=lambda item: item.name))
        if len({item.name for item in entries}) != len(entries):
            raise SkillContractError("skill lock 不能包含重名有效 Skill")
        diagnostics = tuple(sorted(set(str(item) for item in self.diagnostics)))
        payload = {
            "schema_version": 1,
            "entries": [item.to_dict() for item in entries],
            "diagnostics": list(diagnostics),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        declared = str(self.sha256 or "").strip().lower()
        if declared and declared != digest:
            raise SkillContractError("skill lock sha256 与内容不匹配")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "diagnostics", diagnostics)
        object.__setattr__(self, "sha256", digest)

    def to_runtime_json(self) -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "sha256": self.sha256,
                "entries": [item.to_dict() for item in self.entries],
                "diagnostics": list(self.diagnostics),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_runtime_json(cls, raw: object) -> "RuntimeSkillLock":
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 128 * 1024:
            raise SkillContractError("skill lock runtime payload 无效")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SkillContractError("skill lock runtime payload 不是 JSON") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "sha256", "entries", "diagnostics"}
            or payload.get("schema_version") != 1
        ):
            raise SkillContractError("skill lock runtime schema 无效")
        raw_entries = payload.get("entries")
        if not isinstance(raw_entries, list) or len(raw_entries) > 256:
            raise SkillContractError("skill lock entries 无效")
        entries: list[RuntimeSkillLockEntry] = []
        for item in raw_entries:
            if not isinstance(item, dict) or set(item) != {
                "package_id",
                "scope",
                "name",
                "version",
                "description",
                "license_text",
                "compatibility",
                "content_sha256",
                "bundle_sha256",
                "allowed_tools",
                "dependencies",
                "required_permissions",
                "source_kind",
            }:
                raise SkillContractError("skill lock entry 无效")
            for list_field in (
                "allowed_tools",
                "dependencies",
                "required_permissions",
            ):
                values = item.get(list_field)
                if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values
                ):
                    raise SkillContractError(
                        f"skill lock {list_field} 无效"
                    )
            entries.append(
                RuntimeSkillLockEntry(
                    package_id=item.get("package_id", ""),
                    scope=item.get("scope", ""),
                    name=item.get("name", ""),
                    version=item.get("version", ""),
                    description=item.get("description", ""),
                    license_text=item.get("license_text", ""),
                    compatibility=item.get("compatibility", ""),
                    content_sha256=item.get("content_sha256", ""),
                    bundle_sha256=item.get("bundle_sha256", ""),
                    allowed_tools=tuple(item.get("allowed_tools") or ()),
                    dependencies=tuple(item.get("dependencies") or ()),
                    required_permissions=tuple(
                        item.get("required_permissions") or ()
                    ),
                    source_kind=item.get("source_kind", ""),
                )
            )
        diagnostics = payload.get("diagnostics")
        if not isinstance(diagnostics, list) or any(
            not isinstance(item, str) for item in diagnostics
        ):
            raise SkillContractError("skill lock diagnostics 无效")
        return cls(
            entries=tuple(entries),
            diagnostics=tuple(str(item) for item in diagnostics),
            sha256=str(payload.get("sha256") or ""),
        )


def render_skill_catalog(lock: RuntimeSkillLock) -> str:
    """只披露 name/description/version/scope，完整正文由 skill 工具按需加载。"""

    if not lock.entries:
        return ""
    selected: list[dict[str, str]] = []
    omitted = 0
    for entry in lock.entries:
        candidate = {
            "name": entry.name,
            "description": entry.description,
            "version": entry.version,
            "scope": entry.scope.value,
        }
        probe = json.dumps(
            [*selected, candidate],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if selected and len(probe) > SKILL_CATALOG_MAX_BYTES:
            omitted += 1
            continue
        selected.append(candidate)
    payload = json.dumps(
        {"skills": selected, "omitted": omitted, "lock_sha256": lock.sha256},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).replace("<", "\\u003c").replace(">", "\\u003e")
    return (
        '<skill_catalog trust="untrusted_routing_metadata">\n'
        "只有名称和描述常驻上下文；需要时调用本轮 skill 工具读取固定版本正文。\n"
        f"<context_data_json>{payload}</context_data_json>\n"
        "</skill_catalog>"
    )


__all__ = [
    "ParsedSkillBundle",
    "RuntimeSkillLock",
    "RuntimeSkillLockEntry",
    "SKILL_BUNDLE_MAX_BYTES",
    "SKILL_BUNDLE_MAX_FILES",
    "SKILL_CATALOG_MAX_BYTES",
    "SKILL_FILE_MAX_BYTES",
    "SKILL_MD_MAX_BYTES",
    "SkillBundleFile",
    "SkillContractError",
    "SkillScope",
    "SkillScopeTarget",
    "normalize_semver",
    "normalize_skill_name",
    "parse_skill_bundle",
    "render_skill_catalog",
    "semver_key",
    "skill_scope_priority",
]
