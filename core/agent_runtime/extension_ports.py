"""Skill 与 MCP 的框架无关发现 Port。

这里只定义可冻结的内容快照与执行 binding，不包含文件系统路径、传输配置、
endpoint 或凭据。生产发现、安装、MCP transport 和秘密解析留给外层 Adapter。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from core.agent_runtime.contracts import RuntimePrincipal, ToolExecutionPort


_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SKILL_IDENTIFIER_PATTERN = re.compile(
    r"^(?!-)(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*$"
)
_SEMVER_PATTERN = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SKILL_DEPENDENCY_PATTERN = re.compile(
    r"^(?!-)(?!.*--)[a-z0-9]+(?:-[a-z0-9]+)*@"
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SKILL_PERMISSION_PATTERN = re.compile(
    r"^[a-z][a-z0-9_.-]{0,63}:[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"
)
_SKILL_TOOL_PATTERN = re.compile(
    r"^[A-Za-z][A-Za-z0-9_.-]{0,127}(?:\([^\r\n()]{1,128}\))?$"
)
_MCP_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MCP_WIRE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")


def _required(value: object, name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} 不能为空")
    return normalized


def _identifier(value: object, name: str) -> str:
    normalized = _required(value, name)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} 不是合法标识符：{normalized!r}")
    return normalized


def _sha256(value: object, name: str, *, allow_empty: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized and allow_empty:
        return ""
    if len(normalized) != 64 or any(
        char not in "0123456789abcdef" for char in normalized
    ):
        raise ValueError(f"{name} 必须是 64 位十六进制摘要")
    return normalized


def _content_bytes(value: object, name: str) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"{name} 必须是 bytes")
    return bytes(value)


def _snapshot_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"MCP input schema 包含重复字段：{key}")
        result[key] = value
    return result


def mcp_wire_tool_name(server_id: str, tool_name: str) -> str:
    """生成 OpenAI-compatible、带 server namespace 的稳定工具名。"""

    raw = f"{server_id}__{tool_name}"
    normalized = _MCP_WIRE_NAME_PATTERN.sub("_", raw).strip("_")
    if not normalized:
        raise ValueError("MCP 工具无法生成 wire name")
    if len(normalized) <= 64:
        return normalized
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{normalized[:51]}_{digest}"


class RuntimeSkillScope(str, Enum):
    BUILTIN = "builtin"
    PROJECT = "project"
    AGENT = "agent"
    USER = "user"


@dataclass(frozen=True, slots=True)
class RuntimeSkillDescriptor:
    """不暴露来源路径的 Skill 元数据与内容固定点。"""

    provider_id: str
    skill_id: str
    scope: RuntimeSkillScope
    version: str
    description: str
    content_sha256: str
    dependencies: tuple[str, ...] = ()
    required_permissions: tuple[str, ...] = ()
    license_text: str = ""
    compatibility: str = ""
    allowed_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            _identifier(self.provider_id, "skill.provider_id"),
        )
        skill_id = _required(self.skill_id, "skill.skill_id")
        if len(skill_id) > 64 or not _SKILL_IDENTIFIER_PATTERN.fullmatch(skill_id):
            raise ValueError("skill.skill_id 必须符合 Agent Skills name 规范")
        object.__setattr__(self, "skill_id", skill_id)
        try:
            scope = RuntimeSkillScope(self.scope)
        except ValueError as exc:
            raise ValueError("skill.scope 无效") from exc
        object.__setattr__(self, "scope", scope)
        version = _required(self.version, "skill.version")
        if not _SEMVER_PATTERN.fullmatch(version):
            raise ValueError("skill.version 必须是 SemVer")
        object.__setattr__(self, "version", version)
        object.__setattr__(
            self,
            "description",
            _required(self.description, "skill.description"),
        )
        object.__setattr__(
            self,
            "content_sha256",
            _sha256(self.content_sha256, "skill.content_sha256"),
        )
        dependencies = tuple(
            sorted(_required(item, "skill.dependency") for item in self.dependencies)
        )
        permissions = tuple(
            sorted(
                _required(item, "skill.required_permission")
                for item in self.required_permissions
            )
        )
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("skill.dependencies 不能重复")
        if len(permissions) != len(set(permissions)):
            raise ValueError("skill.required_permissions 不能重复")
        if any(not _SKILL_DEPENDENCY_PATTERN.fullmatch(item) for item in dependencies):
            raise ValueError("skill.dependencies 必须使用 name@SemVer")
        if any(not _SKILL_PERMISSION_PATTERN.fullmatch(item) for item in permissions):
            raise ValueError("skill.required_permissions 包含无效声明")
        license_text = str(self.license_text or "").strip()
        compatibility = str(self.compatibility or "").strip()
        if len(license_text) > 512:
            raise ValueError("skill.license_text 过长")
        if len(compatibility) > 500:
            raise ValueError("skill.compatibility 过长")
        allowed_tools = tuple(sorted(_required(item, "skill.allowed_tool") for item in self.allowed_tools))
        if len(allowed_tools) != len(set(allowed_tools)):
            raise ValueError("skill.allowed_tools 不能重复")
        if any(not _SKILL_TOOL_PATTERN.fullmatch(item) for item in allowed_tools):
            raise ValueError("skill.allowed_tools 包含无效声明")
        object.__setattr__(self, "dependencies", dependencies)
        object.__setattr__(self, "required_permissions", permissions)
        object.__setattr__(self, "license_text", license_text)
        object.__setattr__(self, "compatibility", compatibility)
        object.__setattr__(self, "allowed_tools", allowed_tools)

    @property
    def qualified_id(self) -> str:
        return f"{self.provider_id}:{self.scope.value}:{self.skill_id}@{self.version}"


@dataclass(frozen=True, slots=True)
class RuntimeSkillContent:
    descriptor: RuntimeSkillDescriptor
    document: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.descriptor, RuntimeSkillDescriptor):
            raise ValueError("skill descriptor 无效")
        document = _content_bytes(self.document, "skill.document")
        try:
            decoded = document.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("skill.document 必须是 UTF-8 文本") from exc
        if not decoded.strip():
            raise ValueError("skill.document 不能为空")
        if hashlib.sha256(document).hexdigest() != self.descriptor.content_sha256:
            raise ValueError("skill.document 与 content_sha256 不匹配")
        object.__setattr__(self, "document", document)


@dataclass(frozen=True, slots=True)
class RuntimeSkillSnapshot:
    provider_id: str
    revision: str
    skills: tuple[RuntimeSkillDescriptor, ...]
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        provider_id = _identifier(self.provider_id, "skill_snapshot.provider_id")
        revision = _required(self.revision, "skill_snapshot.revision")
        if any(not isinstance(item, RuntimeSkillDescriptor) for item in self.skills):
            raise ValueError("skill_snapshot.skills 包含无效 descriptor")
        skills = tuple(
            sorted(
                self.skills,
                key=lambda item: (
                    item.scope.value,
                    item.skill_id,
                    item.version,
                ),
            )
        )
        active_ids: set[tuple[RuntimeSkillScope, str]] = set()
        for descriptor in skills:
            if descriptor.provider_id != provider_id:
                raise ValueError("Skill descriptor 与 snapshot provider 不一致")
            active_id = (descriptor.scope, descriptor.skill_id)
            if active_id in active_ids:
                raise ValueError("同一 snapshot 不能激活多个同 scope、同 ID 的 Skill")
            active_ids.add(active_id)
        digest = _snapshot_digest(
            {
                "provider_id": provider_id,
                "revision": revision,
                "skills": [
                    {
                        "provider_id": item.provider_id,
                        "skill_id": item.skill_id,
                        "scope": item.scope.value,
                        "version": item.version,
                        "description": item.description,
                        "content_sha256": item.content_sha256,
                        "dependencies": item.dependencies,
                        "required_permissions": item.required_permissions,
                        "license_text": item.license_text,
                        "compatibility": item.compatibility,
                        "allowed_tools": item.allowed_tools,
                    }
                    for item in skills
                ],
            }
        )
        declared = _sha256(
            self.snapshot_sha256,
            "skill_snapshot.snapshot_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise ValueError("skill_snapshot.snapshot_sha256 与内容不匹配")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "skills", skills)
        object.__setattr__(self, "snapshot_sha256", digest)


@runtime_checkable
class SkillProviderPort(Protocol):
    @property
    def provider_id(self) -> str: ...

    async def snapshot(self, *, owner: RuntimePrincipal) -> RuntimeSkillSnapshot: ...

    async def load(
        self,
        descriptor: RuntimeSkillDescriptor,
        *,
        owner: RuntimePrincipal,
    ) -> RuntimeSkillContent: ...


class InMemorySkillProvider:
    """测试用只读 Skill Provider；非 builtin 内容必须显式绑定 owner。"""

    def __init__(
        self,
        provider_id: str,
        *,
        revision: str,
        builtin_contents: tuple[RuntimeSkillContent, ...] = (),
        owner_contents: Mapping[str, tuple[RuntimeSkillContent, ...]] | None = None,
    ) -> None:
        self._provider_id = _identifier(provider_id, "skill_provider.provider_id")
        self._revision = _required(revision, "skill_provider.revision")
        builtin = tuple(builtin_contents)
        for content in builtin:
            self._validate_content(content)
            if content.descriptor.scope is not RuntimeSkillScope.BUILTIN:
                raise ValueError("全局 Skill 内容只能使用 builtin scope")
        scoped: dict[str, tuple[RuntimeSkillContent, ...]] = {}
        for owner_id, contents in (owner_contents or {}).items():
            canonical_owner = _required(owner_id, "skill_provider.owner_id")
            normalized_contents = tuple(contents)
            for content in normalized_contents:
                self._validate_content(content)
                if content.descriptor.scope is RuntimeSkillScope.BUILTIN:
                    raise ValueError("owner Skill 内容不能覆盖 builtin scope")
            scoped[canonical_owner] = normalized_contents
        self._builtin_contents = builtin
        self._owner_contents = MappingProxyType(scoped)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _validate_content(self, content: RuntimeSkillContent) -> None:
        if not isinstance(content, RuntimeSkillContent):
            raise TypeError("Skill Provider 内容必须是 RuntimeSkillContent")
        if content.descriptor.provider_id != self.provider_id:
            raise ValueError("Skill 内容与 Provider ID 不一致")

    def _visible_contents(
        self,
        owner: RuntimePrincipal,
    ) -> tuple[RuntimeSkillContent, ...]:
        if not isinstance(owner, RuntimePrincipal):
            raise ValueError("Skill owner 无效")
        return self._builtin_contents + self._owner_contents.get(
            owner.canonical_id,
            (),
        )

    async def snapshot(self, *, owner: RuntimePrincipal) -> RuntimeSkillSnapshot:
        return RuntimeSkillSnapshot(
            provider_id=self.provider_id,
            revision=self._revision,
            skills=tuple(
                content.descriptor for content in self._visible_contents(owner)
            ),
        )

    async def load(
        self,
        descriptor: RuntimeSkillDescriptor,
        *,
        owner: RuntimePrincipal,
    ) -> RuntimeSkillContent:
        if not isinstance(descriptor, RuntimeSkillDescriptor):
            raise ValueError("Skill descriptor 无效")
        for content in self._visible_contents(owner):
            if content.descriptor == descriptor:
                return content
        raise PermissionError("Skill 不存在或 owner 未授权")


@dataclass(frozen=True, slots=True)
class RuntimeMcpToolDescriptor:
    """MCP 工具的命名空间、原始 schema 快照与执行 binding。"""

    provider_id: str
    server_id: str
    tool_name: str
    input_schema_json: bytes
    execution_port_id: str
    description: str = ""
    input_schema_sha256: str = ""
    read_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_id",
            _identifier(self.provider_id, "mcp_tool.provider_id"),
        )
        object.__setattr__(
            self,
            "server_id",
            _identifier(self.server_id, "mcp_tool.server_id"),
        )
        tool_name = _required(self.tool_name, "mcp_tool.tool_name")
        if not _MCP_TOOL_NAME_PATTERN.fullmatch(tool_name):
            raise ValueError(f"mcp_tool.tool_name 不是合法名称：{tool_name!r}")
        object.__setattr__(self, "tool_name", tool_name)
        schema = _content_bytes(self.input_schema_json, "mcp_tool.input_schema_json")
        try:
            parsed = json.loads(
                schema.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("mcp_tool.input_schema_json 必须是 UTF-8 JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("mcp_tool.input_schema_json 顶层必须是对象")
        digest = hashlib.sha256(schema).hexdigest()
        declared = _sha256(
            self.input_schema_sha256,
            "mcp_tool.input_schema_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise ValueError("MCP input schema 与 input_schema_sha256 不匹配")
        object.__setattr__(self, "input_schema_json", schema)
        object.__setattr__(self, "input_schema_sha256", digest)
        object.__setattr__(
            self,
            "execution_port_id",
            _required(self.execution_port_id, "mcp_tool.execution_port_id"),
        )
        object.__setattr__(
            self,
            "description",
            str(self.description or "").strip(),
        )
        if not isinstance(self.read_only, bool):
            raise ValueError("mcp_tool.read_only 必须是 bool")

    @property
    def qualified_name(self) -> str:
        return f"{self.server_id}:{self.tool_name}"

    @property
    def wire_name(self) -> str:
        return mcp_wire_tool_name(self.server_id, self.tool_name)


@dataclass(frozen=True, slots=True)
class RuntimeMcpSnapshot:
    provider_id: str
    revision: str
    tools: tuple[RuntimeMcpToolDescriptor, ...]
    snapshot_sha256: str = ""

    def __post_init__(self) -> None:
        provider_id = _identifier(self.provider_id, "mcp_snapshot.provider_id")
        revision = _required(self.revision, "mcp_snapshot.revision")
        if any(not isinstance(item, RuntimeMcpToolDescriptor) for item in self.tools):
            raise ValueError("mcp_snapshot.tools 包含无效 descriptor")
        tools = tuple(
            sorted(
                self.tools,
                key=lambda item: (item.server_id, item.tool_name),
            )
        )
        identities: set[tuple[str, str]] = set()
        wire_names: set[str] = set()
        for descriptor in tools:
            if descriptor.provider_id != provider_id:
                raise ValueError("MCP tool descriptor 与 snapshot provider 不一致")
            identity = (descriptor.server_id, descriptor.tool_name)
            if identity in identities:
                raise ValueError("同一 MCP server 内不能注册重名工具")
            identities.add(identity)
            if descriptor.wire_name in wire_names:
                raise ValueError("同一 MCP server 内工具 wire name 冲突")
            wire_names.add(descriptor.wire_name)
        digest = _snapshot_digest(
            {
                "provider_id": provider_id,
                "revision": revision,
                "tools": [
                    {
                        "server_id": item.server_id,
                        "tool_name": item.tool_name,
                        "description": item.description,
                        "input_schema_sha256": item.input_schema_sha256,
                        "execution_port_id": item.execution_port_id,
                        "wire_name": item.wire_name,
                        "read_only": item.read_only,
                    }
                    for item in tools
                ],
            }
        )
        declared = _sha256(
            self.snapshot_sha256,
            "mcp_snapshot.snapshot_sha256",
            allow_empty=True,
        )
        if declared and declared != digest:
            raise ValueError("mcp_snapshot.snapshot_sha256 与内容不匹配")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "revision", revision)
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "snapshot_sha256", digest)


@runtime_checkable
class McpProviderPort(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def tool_execution_port(self) -> ToolExecutionPort: ...

    async def snapshot(self, *, owner: RuntimePrincipal) -> RuntimeMcpSnapshot: ...


class InMemoryMcpProvider:
    """测试用静态 MCP Provider；不实现 transport、配置或秘密解析。"""

    def __init__(
        self,
        provider_id: str,
        *,
        revision: str,
        tools: tuple[RuntimeMcpToolDescriptor, ...],
        tool_execution_port: ToolExecutionPort,
    ) -> None:
        self._provider_id = _identifier(provider_id, "mcp_provider.provider_id")
        if not isinstance(tool_execution_port, ToolExecutionPort):
            raise TypeError("MCP Provider 必须绑定 ToolExecutionPort")
        self._tool_execution_port = tool_execution_port
        self._snapshot = RuntimeMcpSnapshot(
            provider_id=self.provider_id,
            revision=revision,
            tools=tuple(tools),
        )

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def tool_execution_port(self) -> ToolExecutionPort:
        return self._tool_execution_port

    async def snapshot(self, *, owner: RuntimePrincipal) -> RuntimeMcpSnapshot:
        if not isinstance(owner, RuntimePrincipal):
            raise ValueError("MCP owner 无效")
        return self._snapshot


__all__ = [
    "InMemoryMcpProvider",
    "InMemorySkillProvider",
    "McpProviderPort",
    "mcp_wire_tool_name",
    "RuntimeMcpSnapshot",
    "RuntimeMcpToolDescriptor",
    "RuntimeSkillContent",
    "RuntimeSkillDescriptor",
    "RuntimeSkillScope",
    "RuntimeSkillSnapshot",
    "SkillProviderPort",
]
