"""受信内置目录与数据库绑定的生产 Skill Provider。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from core.agent_runtime.contracts import RuntimeOwnerType, RuntimePrincipal
from core.agent_runtime.extension_ports import (
    RuntimeSkillContent,
    RuntimeSkillDescriptor,
    RuntimeSkillScope,
    RuntimeSkillSnapshot,
)
from core.skills.contracts import (
    ParsedSkillBundle,
    RuntimeSkillLock,
    RuntimeSkillLockEntry,
    SKILL_BUNDLE_MAX_BYTES,
    SKILL_BUNDLE_MAX_FILES,
    SKILL_FILE_MAX_BYTES,
    SKILL_MD_MAX_BYTES,
    SkillBundleFile,
    SkillContractError,
    SkillScope,
    SkillScopeTarget,
    parse_skill_bundle,
    skill_scope_priority,
)
from core.skills.service import LoadedSkillContent, SkillLifecycleService


@dataclass(frozen=True, slots=True)
class BundledSkillRecord:
    entry: RuntimeSkillLockEntry
    bundle: ParsedSkillBundle


class BundledSkillCatalog:
    """只扫描 operator 控制的固定发布目录，不扫描 cwd 或用户 HOME。"""

    def __init__(self, root: Path) -> None:
        candidate = Path(root)
        if not candidate.is_absolute():
            raise SkillContractError("bundled Skill root 必须是绝对路径")
        if candidate.is_symlink():
            raise SkillContractError("bundled Skill root 禁止符号链接")
        self._root = candidate.resolve(strict=False)
        self._records: dict[str, BundledSkillRecord] = {}
        self._diagnostics: tuple[str, ...] = ()
        self._scan()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._diagnostics

    def records(self) -> tuple[BundledSkillRecord, ...]:
        return tuple(
            sorted(self._records.values(), key=lambda item: item.entry.name)
        )

    def get(self, package_id: str) -> BundledSkillRecord | None:
        return self._records.get(str(package_id or "").strip())

    @staticmethod
    def _read_bounded_file(
        path: Path,
        *,
        max_bytes: int,
        field: str,
    ) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise SkillContractError(f"{field} 必须是非链接普通文件")
        if path.stat().st_size > max_bytes:
            raise SkillContractError(f"{field} 超过大小上限")
        with path.open("rb") as handle:
            content = handle.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise SkillContractError(f"{field} 超过大小上限")
        return content

    def _resource_files(
        self,
        skill_root: Path,
        *,
        initial_size: int,
    ) -> tuple[SkillBundleFile, ...]:
        paths: list[Path] = []
        for child in sorted(
            skill_root.rglob("*"),
            key=lambda item: item.as_posix(),
        ):
            if child.is_symlink():
                raise SkillContractError("bundled Skill 禁止符号链接")
            if child.name == "SKILL.md" or child.is_dir():
                continue
            if not child.is_file():
                raise SkillContractError("bundled Skill 资源必须是普通文件")
            paths.append(child)
        if len(paths) > SKILL_BUNDLE_MAX_FILES:
            raise SkillContractError("bundled Skill 资源文件数量超过上限")
        total_size = initial_size
        resources: list[SkillBundleFile] = []
        for path in paths:
            stat_size = path.stat().st_size
            if stat_size > SKILL_FILE_MAX_BYTES:
                raise SkillContractError("bundled Skill 单个资源超过大小上限")
            if total_size + stat_size > SKILL_BUNDLE_MAX_BYTES:
                raise SkillContractError("bundled Skill 包超过总大小上限")
            content = self._read_bounded_file(
                path,
                max_bytes=SKILL_FILE_MAX_BYTES,
                field="bundled Skill 资源",
            )
            total_size += len(content)
            if total_size > SKILL_BUNDLE_MAX_BYTES:
                raise SkillContractError("bundled Skill 包超过总大小上限")
            resources.append(
                SkillBundleFile(
                    relative_path=path.relative_to(skill_root).as_posix(),
                    content=content,
                )
            )
        return tuple(resources)

    def _scan(self) -> None:
        if not self._root.exists():
            self._records = {}
            self._diagnostics = ()
            return
        if self._root.is_symlink() or not self._root.is_dir():
            raise SkillContractError("bundled Skill root 必须是非链接目录")
        records: dict[str, BundledSkillRecord] = {}
        names: set[str] = set()
        diagnostics: list[str] = []
        for directory in sorted(
            self._root.iterdir(),
            key=lambda item: item.name,
        ):
            try:
                if directory.is_symlink():
                    raise SkillContractError("bundled Skill 禁止符号链接")
                if not directory.is_dir():
                    continue
                skill_md = directory / "SKILL.md"
                if not skill_md.is_file() or skill_md.is_symlink():
                    continue
                skill_md_content = self._read_bounded_file(
                    skill_md,
                    max_bytes=SKILL_MD_MAX_BYTES,
                    field="bundled SKILL.md",
                )
                bundle = parse_skill_bundle(
                    skill_md_content,
                    files=self._resource_files(
                        directory,
                        initial_size=len(skill_md_content),
                    ),
                    expected_name=directory.name,
                )
                if bundle.name in names:
                    raise SkillContractError("bundled Skill 重名")
                names.add(bundle.name)
                entry = RuntimeSkillLockEntry(
                    package_id=f"bundled_{bundle.bundle_sha256}",
                    scope=SkillScope.BUILTIN,
                    name=bundle.name,
                    version=bundle.version,
                    description=bundle.description,
                    license_text=bundle.license_text,
                    compatibility=bundle.compatibility,
                    content_sha256=bundle.skill_md_sha256,
                    bundle_sha256=bundle.bundle_sha256,
                    allowed_tools=bundle.allowed_tools,
                    dependencies=bundle.dependencies,
                    required_permissions=bundle.required_permissions,
                    capability_tags=bundle.capability_tags,
                    applies_to=bundle.applies_to,
                    body_prompt_tokens=bundle.body_prompt_tokens,
                    catalog_prompt_tokens=bundle.catalog_prompt_tokens,
                    source_kind="bundled",
                )
                records[entry.package_id] = BundledSkillRecord(entry, bundle)
            except (OSError, SkillContractError) as exc:
                diagnostics.append(
                    f"bundled_invalid:{directory.name}:{type(exc).__name__}"
                )
        self._records = records
        self._diagnostics = tuple(sorted(diagnostics))


def default_bundled_skill_catalog() -> BundledSkillCatalog:
    root = (
        Path(__file__).resolve().parents[2]
        / "creatures"
        / "nanobot"
        / "skills"
    )
    return BundledSkillCatalog(root)


@dataclass(frozen=True, slots=True)
class SkillResolutionContext:
    targets: tuple[SkillScopeTarget, ...]
    executable_tool_names: frozenset[str]

    def __post_init__(self) -> None:
        targets = tuple(self.targets)
        if any(not isinstance(item, SkillScopeTarget) for item in targets):
            raise SkillContractError("Skill resolution targets 无效")
        if len({(item.scope, item.scope_key) for item in targets}) != len(targets):
            raise SkillContractError("Skill resolution targets 不能重复")
        if len({item.scope for item in targets}) != len(targets):
            raise SkillContractError("同一请求每个 Skill scope 只能有一个目标")
        tools = frozenset(
            str(item or "").strip()
            for item in self.executable_tool_names
            if str(item or "").strip()
        )
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "executable_tool_names", tools)


def _tool_base(value: str) -> str:
    return str(value or "").split("(", 1)[0].strip()


def _permission_allowed(permission: str, tools: frozenset[str]) -> bool:
    namespace, _, value = permission.partition(":")
    if namespace == "tool":
        return value in tools
    if permission == "network:none":
        return True
    if permission == "skill:resource-read":
        return True
    if permission == "workspace:read":
        return "workspace_read" in tools
    if permission == "workspace:write":
        return bool({"workspace_write", "workspace_edit"} & tools)
    if permission == "network:search":
        return "web_search" in tools
    if permission == "sandbox:execute":
        return "sandbox_exec" in tools
    return False


def _cycle_names(entries: dict[str, RuntimeSkillLockEntry]) -> set[str]:
    graph = {
        name: tuple(
            dependency.rsplit("@", 1)[0]
            for dependency in entry.dependencies
        )
        for name, entry in entries.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    cycles: set[str] = set()

    def visit(name: str, path: tuple[str, ...]) -> None:
        if name in visiting:
            start = path.index(name) if name in path else 0
            cycles.update(path[start:])
            return
        if name in visited:
            return
        visiting.add(name)
        for dependency in graph.get(name, ()):
            if dependency in graph:
                visit(dependency, (*path, dependency))
        visiting.remove(name)
        visited.add(name)

    for name in sorted(graph):
        visit(name, (name,))
    return cycles


class SqlAlchemySkillProvider:
    """生成请求级精确版本锁，并按锁加载正文或单一资源。"""

    provider_id = "nanobot-skills"

    def __init__(
        self,
        db: Session,
        *,
        bundled_catalog: BundledSkillCatalog | None = None,
    ) -> None:
        self._service = SkillLifecycleService(db)
        self._bundled = bundled_catalog or default_bundled_skill_catalog()

    def resolve_lock(self, context: SkillResolutionContext) -> RuntimeSkillLock:
        candidates: list[RuntimeSkillLockEntry] = [
            record.entry for record in self._bundled.records()
        ]
        candidates.extend(
            self._service.lock_entry_from_package(package)
            for package in self._service.active_packages(context.targets)
        )
        diagnostics = list(self._bundled.diagnostics)
        grouped: dict[str, list[RuntimeSkillLockEntry]] = {}
        for entry in candidates:
            grouped.setdefault(entry.name, []).append(entry)
        selected: dict[str, RuntimeSkillLockEntry] = {}
        for name, items in sorted(grouped.items()):
            ordered = sorted(
                items,
                key=lambda item: (
                    skill_scope_priority(item.scope),
                    item.version,
                    item.bundle_sha256,
                ),
                reverse=True,
            )
            selected[name] = ordered[0]
            diagnostics.extend(
                f"shadowed:{name}:{item.scope.value}"
                for item in ordered[1:]
            )

        for name, entry in tuple(selected.items()):
            required_tools = {_tool_base(item) for item in entry.allowed_tools}
            missing_tools = sorted(
                required_tools - context.executable_tool_names
            )
            denied_permissions = sorted(
                item
                for item in entry.required_permissions
                if not _permission_allowed(
                    item,
                    context.executable_tool_names,
                )
            )
            if missing_tools or denied_permissions:
                selected.pop(name)
                diagnostics.append(f"permission_denied:{name}")

        changed = True
        while changed:
            changed = False
            for name, entry in tuple(selected.items()):
                missing = []
                for dependency in entry.dependencies:
                    dependency_name, dependency_version = dependency.rsplit(
                        "@",
                        1,
                    )
                    resolved = selected.get(dependency_name)
                    if resolved is None or resolved.version != dependency_version:
                        missing.append(dependency)
                if missing:
                    selected.pop(name)
                    diagnostics.append(f"dependency_missing:{name}")
                    changed = True

        cycles = _cycle_names(selected)
        for name in sorted(cycles):
            selected.pop(name, None)
            diagnostics.append(f"dependency_cycle:{name}")
        if cycles:
            changed = True
            while changed:
                changed = False
                for name, entry in tuple(selected.items()):
                    if any(
                        dependency.rsplit("@", 1)[0] not in selected
                        for dependency in entry.dependencies
                    ):
                        selected.pop(name)
                        diagnostics.append(f"dependency_missing:{name}")
                        changed = True

        return RuntimeSkillLock(
            entries=tuple(selected.values()),
            diagnostics=tuple(diagnostics),
        )

    def load_locked(
        self,
        entry: RuntimeSkillLockEntry,
        *,
        visible_targets: tuple[SkillScopeTarget, ...],
        resource_path: str = "",
    ) -> LoadedSkillContent:
        if entry.source_kind == "managed":
            return self._service.load_managed(
                entry,
                visible_targets=visible_targets,
                resource_path=resource_path,
            )
        record = self._bundled.get(entry.package_id)
        if record is None or record.entry != entry:
            raise SkillContractError("bundled Skill lock 已漂移")
        if not resource_path:
            return LoadedSkillContent(
                entry=entry,
                body=record.bundle.body,
                resource_paths=record.bundle.resource_paths,
            )
        normalized = SkillBundleFile(
            resource_path,
            b"validation-placeholder",
        ).relative_path
        resource = next(
            (
                item
                for item in record.bundle.files
                if item.relative_path == normalized
            ),
            None,
        )
        if resource is None:
            raise SkillContractError("bundled Skill 资源不存在")
        return LoadedSkillContent(
            entry=entry,
            body=record.bundle.body,
            resource_paths=record.bundle.resource_paths,
            resource_path=resource.relative_path,
            resource_media_type=resource.media_type,
            resource_content=resource.content,
        )

    def _owner_context(self, owner: RuntimePrincipal) -> SkillResolutionContext:
        if not isinstance(owner, RuntimePrincipal):
            raise SkillContractError("Skill owner 无效")
        targets: list[SkillScopeTarget] = [
            SkillScopeTarget("builtin", "builtin")
        ]
        if owner.owner_type is RuntimeOwnerType.USER:
            targets.append(SkillScopeTarget("user", owner.canonical_id))
        from core.tool_registration import list_active_tool_registrations

        return SkillResolutionContext(
            targets=tuple(targets),
            executable_tool_names=frozenset(
                registration.name
                for registration in list_active_tool_registrations()
            ),
        )

    async def snapshot(self, *, owner: RuntimePrincipal) -> RuntimeSkillSnapshot:
        lock = self.resolve_lock(self._owner_context(owner))
        return RuntimeSkillSnapshot(
            provider_id=self.provider_id,
            revision=lock.sha256,
            skills=tuple(
                RuntimeSkillDescriptor(
                    provider_id=self.provider_id,
                    skill_id=entry.name,
                    scope=RuntimeSkillScope(entry.scope.value),
                    version=entry.version,
                    description=entry.description,
                    content_sha256=entry.content_sha256,
                    dependencies=entry.dependencies,
                    required_permissions=entry.required_permissions,
                    allowed_tools=entry.allowed_tools,
                    license_text=entry.license_text,
                    compatibility=entry.compatibility,
                )
                for entry in lock.entries
            ),
        )

    async def load(
        self,
        descriptor: RuntimeSkillDescriptor,
        *,
        owner: RuntimePrincipal,
    ) -> RuntimeSkillContent:
        owner_context = self._owner_context(owner)
        lock = self.resolve_lock(owner_context)
        entry = next(
            (
                item
                for item in lock.entries
                if item.name == descriptor.skill_id
                and item.version == descriptor.version
                and item.content_sha256 == descriptor.content_sha256
            ),
            None,
        )
        if entry is None:
            raise PermissionError("Skill 不存在或 owner 未授权")
        self.load_locked(
            entry,
            visible_targets=owner_context.targets,
        )
        if entry.source_kind == "managed":
            package = self._service.package_by_id(entry.package_id)
            if package is None:
                raise PermissionError("Skill 不存在或 owner 未授权")
            document = bytes(package.skill_md)
        else:
            record = self._bundled.get(entry.package_id)
            if record is None:
                raise PermissionError("Skill 不存在或 owner 未授权")
            document = record.bundle.skill_md
        if hashlib.sha256(document).hexdigest() != entry.content_sha256:
            raise SkillContractError("Skill 内容固定点已漂移")
        return RuntimeSkillContent(descriptor=descriptor, document=document)


def runtime_skill_targets(
    *,
    platform: str,
    is_group: bool,
    owner_id: str,
    agent_id: str,
    project_id: str = "",
) -> tuple[SkillScopeTarget, ...]:
    """从受信运行时身份生成可见作用域；群聊不注入用户私有 Skill。"""

    targets: list[SkillScopeTarget] = [SkillScopeTarget("builtin", "builtin")]
    agent = str(agent_id or "").strip()
    if agent:
        targets.append(SkillScopeTarget("agent", agent))
    user = str(owner_id or "").strip()
    if not is_group and user:
        targets.append(SkillScopeTarget("user", f"{platform}:user:{user}"))
    project = str(project_id or "").strip()
    if project:
        targets.append(SkillScopeTarget("project", project))
    return tuple(targets)


__all__ = [
    "BundledSkillCatalog",
    "BundledSkillRecord",
    "SkillResolutionContext",
    "SqlAlchemySkillProvider",
    "default_bundled_skill_catalog",
    "runtime_skill_targets",
]
