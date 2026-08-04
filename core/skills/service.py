"""受管 Agent Skill 版本安装、切换、回滚与精确内容读取。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models.skill import (
    SkillBindingRow,
    SkillLifecycleEventRow,
    SkillPackageFileRow,
    SkillPackageRow,
)
from core.skills.contracts import (
    ParsedSkillBundle,
    RuntimeSkillLockEntry,
    SkillBundleFile,
    SkillContractError,
    SkillScope,
    SkillScopeTarget,
    normalize_semver,
    normalize_skill_name,
    parse_skill_bundle,
    semver_key,
)


class SkillLifecycleError(RuntimeError):
    """Skill 生命周期操作无法满足当前状态。"""


class SkillVersionConflictError(SkillLifecycleError):
    """同版本正文漂移或乐观版本竞争。"""


class SkillNotFoundError(SkillLifecycleError):
    """目标 Skill、版本或资源不存在。"""


@dataclass(frozen=True, slots=True)
class SkillBindingSnapshot:
    binding_id: str
    target: SkillScopeTarget
    skill_name: str
    status: str
    pinned: bool
    trusted: bool
    generation: int
    active_package_id: str
    active_version: str
    active_bundle_sha256: str
    previous_package_id: str
    previous_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_id": self.binding_id,
            "scope": self.target.scope.value,
            "scope_key": self.target.scope_key,
            "skill_name": self.skill_name,
            "status": self.status,
            "pinned": self.pinned,
            "trusted": self.trusted,
            "generation": self.generation,
            "active_package_id": self.active_package_id,
            "active_version": self.active_version,
            "active_bundle_sha256": self.active_bundle_sha256,
            "previous_package_id": self.previous_package_id,
            "previous_version": self.previous_version,
        }


@dataclass(frozen=True, slots=True)
class SkillVersionSnapshot:
    package_id: str
    target: SkillScopeTarget
    skill_name: str
    version: str
    description: str
    bundle_sha256: str
    source_kind: str
    source_label: str
    trusted: bool
    file_count: int
    bundle_size: int
    capability_tags: tuple[str, ...]
    applies_to: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    dependencies: tuple[str, ...]
    required_permissions: tuple[str, ...]
    body_prompt_tokens: int
    catalog_prompt_tokens: int

    def to_dict(self) -> dict[str, object]:
        return {
            "package_id": self.package_id,
            "scope": self.target.scope.value,
            "scope_key": self.target.scope_key,
            "skill_name": self.skill_name,
            "version": self.version,
            "description": self.description,
            "bundle_sha256": self.bundle_sha256,
            "source_kind": self.source_kind,
            "source_label": self.source_label,
            "trusted": self.trusted,
            "file_count": self.file_count,
            "bundle_size": self.bundle_size,
            "capability_tags": list(self.capability_tags),
            "applies_to": list(self.applies_to),
            "allowed_tools": list(self.allowed_tools),
            "dependencies": list(self.dependencies),
            "required_permissions": list(self.required_permissions),
            "body_prompt_tokens": self.body_prompt_tokens,
            "catalog_prompt_tokens": self.catalog_prompt_tokens,
        }


@dataclass(frozen=True, slots=True)
class LoadedSkillContent:
    entry: RuntimeSkillLockEntry
    body: str
    resource_paths: tuple[str, ...]
    resource_path: str = ""
    resource_media_type: str = ""
    resource_content: bytes = b""


def _actor(value: object) -> str:
    actor = str(value or "").strip()
    if not actor or len(actor) > 255 or any(ord(char) < 32 for char in actor):
        raise SkillContractError("skill actor_id 无效")
    return actor


def _source_label(value: object) -> str:
    label = str(value or "manual-upload").strip()
    if not label or len(label) > 255 or any(ord(char) < 32 for char in label):
        raise SkillContractError("skill source_label 无效")
    return label


def _json_tuple(raw: object, field: str) -> tuple[str, ...]:
    try:
        value = json.loads(str(raw or "[]"))
    except json.JSONDecodeError as exc:
        raise SkillLifecycleError(f"{field} 持久化 JSON 已损坏") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SkillLifecycleError(f"{field} 持久化 JSON 已损坏")
    return tuple(value)


def _event_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class SkillLifecycleService:
    """只接受字面 bundle；无下载器、无 subprocess、无自动安装命令。"""

    def __init__(self, db: Session) -> None:
        if not isinstance(db, Session):
            raise TypeError("db 必须是 SQLAlchemy Session")
        self._db = db

    def _binding(
        self,
        target: SkillScopeTarget,
        skill_name: str,
    ) -> SkillBindingRow | None:
        return self._db.execute(
            select(SkillBindingRow).where(
                SkillBindingRow.scope == target.scope.value,
                SkillBindingRow.scope_key == target.scope_key,
                SkillBindingRow.skill_name == skill_name,
            )
        ).scalar_one_or_none()

    def _package(
        self,
        target: SkillScopeTarget,
        skill_name: str,
        version: str,
    ) -> SkillPackageRow | None:
        return self._db.execute(
            select(SkillPackageRow).where(
                SkillPackageRow.scope == target.scope.value,
                SkillPackageRow.scope_key == target.scope_key,
                SkillPackageRow.skill_name == skill_name,
                SkillPackageRow.version == version,
            )
        ).scalar_one_or_none()

    def package_by_id(self, package_id: str) -> SkillPackageRow | None:
        """按不可变 package_id 读取版本；调用方仍须自行校验可见作用域。"""

        return self._db.get(SkillPackageRow, str(package_id or "").strip())

    @staticmethod
    def _require_package_projection(
        package: SkillPackageRow | None,
        *,
        target: SkillScopeTarget,
        skill_name: str,
    ) -> SkillPackageRow:
        if package is None:
            raise SkillLifecycleError("Skill binding 指向缺失版本")
        if (
            package.scope != target.scope.value
            or package.scope_key != target.scope_key
            or package.skill_name != skill_name
            or package.source_kind != "managed"
            or not bool(package.trusted)
        ):
            raise SkillLifecycleError("Skill binding 与受管版本投影不一致")
        return package

    def _snapshot(self, binding: SkillBindingRow) -> SkillBindingSnapshot:
        target = SkillScopeTarget(binding.scope, binding.scope_key)
        skill_name = normalize_skill_name(binding.skill_name)
        active = self.package_by_id(binding.active_package_id or "")
        previous = self.package_by_id(binding.previous_package_id or "")
        if binding.status == "active":
            active = self._require_package_projection(
                active,
                target=target,
                skill_name=skill_name,
            )
        elif active is not None:
            raise SkillLifecycleError("已卸载 Skill 仍指向激活版本")
        if binding.previous_package_id:
            previous = self._require_package_projection(
                previous,
                target=target,
                skill_name=skill_name,
            )
        return SkillBindingSnapshot(
            binding_id=str(binding.binding_id),
            target=target,
            skill_name=skill_name,
            status=str(binding.status),
            pinned=bool(binding.pinned),
            trusted=bool(binding.trusted),
            generation=int(binding.generation),
            active_package_id=str(binding.active_package_id or ""),
            active_version=str(active.version if active is not None else ""),
            active_bundle_sha256=str(
                active.bundle_sha256 if active is not None else ""
            ),
            previous_package_id=str(binding.previous_package_id or ""),
            previous_version=str(
                previous.version if previous is not None else ""
            ),
        )

    @staticmethod
    def _require_managed_target(target: SkillScopeTarget) -> None:
        if target.scope is SkillScope.BUILTIN:
            raise SkillLifecycleError("builtin Skill 只能随受信发布物升级")

    @staticmethod
    def _require_generation(
        binding: SkillBindingRow,
        expected_generation: int | None,
    ) -> int:
        if type(expected_generation) is not int or expected_generation < 1:
            raise SkillVersionConflictError("必须提供正整数 expected_generation")
        if int(binding.generation) != expected_generation:
            raise SkillVersionConflictError("Skill binding generation 已变化")
        return expected_generation

    def _append_event(
        self,
        binding: SkillBindingRow,
        *,
        event_kind: str,
        generation: int,
        previous_package_id: str,
        current_package_id: str,
        previous_pinned: bool,
        current_pinned: bool,
        actor_id: str,
    ) -> None:
        payload: dict[str, object] = {
            "binding_id": str(binding.binding_id),
            "event_kind": event_kind,
            "generation": generation,
            "previous_package_id": previous_package_id,
            "current_package_id": current_package_id,
            "previous_pinned": previous_pinned,
            "current_pinned": current_pinned,
            "actor_id": actor_id,
        }
        self._db.add(
            SkillLifecycleEventRow(
                event_id=f"skillevt_{uuid.uuid4().hex}",
                binding_id=binding.binding_id,
                generation=generation,
                event_kind=event_kind,
                previous_package_id=previous_package_id,
                current_package_id=current_package_id,
                previous_pinned=previous_pinned,
                current_pinned=current_pinned,
                actor_id=actor_id,
                event_sha256=_event_digest(payload),
            )
        )

    def _cas_update(
        self,
        binding: SkillBindingRow,
        *,
        expected_generation: int,
        values: dict[str, object],
    ) -> SkillBindingRow:
        now = datetime.now()
        next_generation = expected_generation + 1
        result = self._db.execute(
            update(SkillBindingRow)
            .where(
                SkillBindingRow.binding_id == binding.binding_id,
                SkillBindingRow.generation == expected_generation,
            )
            .values(
                **values,
                generation=next_generation,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise SkillVersionConflictError("Skill binding 并发更新冲突")
        self._db.flush()
        self._db.expire_all()
        updated = self._db.get(SkillBindingRow, binding.binding_id)
        if updated is None or int(updated.generation) != next_generation:
            raise SkillVersionConflictError("Skill binding 更新后投影缺失")
        return updated

    def _create_package(
        self,
        target: SkillScopeTarget,
        bundle: ParsedSkillBundle,
        *,
        actor_id: str,
        source_kind: str,
        source_label: str,
        trusted: bool,
    ) -> SkillPackageRow:
        package = SkillPackageRow(
            package_id=f"skillpkg_{uuid.uuid4().hex}",
            scope=target.scope.value,
            scope_key=target.scope_key,
            skill_name=bundle.name,
            version=bundle.version,
            description=bundle.description,
            license_text=bundle.license_text,
            compatibility=bundle.compatibility,
            metadata_json=json.dumps(
                dict(bundle.metadata),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            allowed_tools_json=json.dumps(list(bundle.allowed_tools)),
            dependencies_json=json.dumps(list(bundle.dependencies)),
            required_permissions_json=json.dumps(
                list(bundle.required_permissions)
            ),
            skill_md=bundle.skill_md,
            skill_md_sha256=bundle.skill_md_sha256,
            skill_md_size=len(bundle.skill_md),
            bundle_sha256=bundle.bundle_sha256,
            bundle_size=bundle.bundle_size,
            file_count=len(bundle.files),
            source_kind=source_kind,
            source_label=source_label,
            trusted=trusted,
            created_by=actor_id,
        )
        self._db.add(package)
        try:
            # ORM 未声明双向 relationship，不能依赖 UnitOfWork 自动推断
            # 两张表的 INSERT 顺序；先落父记录，确保开启外键的 SQLite 与
            # 生产数据库都不会先写 skill_package_files。
            self._db.flush()
            for item in bundle.files:
                self._db.add(
                    SkillPackageFileRow(
                        package_id=package.package_id,
                        relative_path=item.relative_path,
                        media_type=item.media_type,
                        content=item.content,
                        content_sha256=item.content_sha256,
                        size_bytes=len(item.content),
                    )
                )
            self._db.flush()
        except IntegrityError as exc:
            raise SkillVersionConflictError("Skill 版本并发安装冲突") from exc
        return package

    def install(
        self,
        target: SkillScopeTarget,
        bundle: ParsedSkillBundle,
        *,
        actor_id: str,
        source_label: str = "manual-upload",
        trusted_source: bool,
        pin: bool = True,
        expected_generation: int | None = None,
    ) -> SkillBindingSnapshot:
        """保存版本；首次安装激活，后续版本必须通过 upgrade 显式切换。"""

        self._require_managed_target(target)
        if not isinstance(bundle, ParsedSkillBundle):
            raise TypeError("bundle 必须是 ParsedSkillBundle")
        if not trusted_source:
            raise SkillLifecycleError("来源未由管理员标记为受信，拒绝安装")
        actor = _actor(actor_id)
        label = _source_label(source_label)
        package = self._package(target, bundle.name, bundle.version)
        package_existed = package is not None
        if package is not None:
            package = self._require_package_projection(
                package,
                target=target,
                skill_name=bundle.name,
            )
        if package is not None and package.bundle_sha256 != bundle.bundle_sha256:
            raise SkillVersionConflictError("同一 Skill 版本正文不可变")
        binding = self._binding(target, bundle.name)
        if package_existed and binding is not None and binding.status == "active":
            return self._snapshot(binding)
        if package is None:
            package = self._create_package(
                target,
                bundle,
                actor_id=actor,
                source_kind="managed",
                source_label=label,
                trusted=True,
            )
        if binding is None:
            binding = SkillBindingRow(
                binding_id=f"skillbind_{uuid.uuid4().hex}",
                scope=target.scope.value,
                scope_key=target.scope_key,
                skill_name=bundle.name,
                active_package_id=package.package_id,
                previous_package_id=None,
                status="active",
                pinned=bool(pin),
                trusted=True,
                generation=1,
                updated_by=actor,
            )
            self._db.add(binding)
            try:
                self._db.flush()
            except IntegrityError as exc:
                raise SkillVersionConflictError(
                    "Skill binding 并发安装冲突"
                ) from exc
            self._append_event(
                binding,
                event_kind="installed",
                generation=1,
                previous_package_id="",
                current_package_id=package.package_id,
                previous_pinned=False,
                current_pinned=bool(pin),
                actor_id=actor,
            )
            return self._snapshot(binding)
        generation = self._require_generation(binding, expected_generation)
        previous_active = str(binding.active_package_id or "")
        previous_package = str(binding.previous_package_id or "")
        previous_pinned = bool(binding.pinned)
        if binding.status == "uninstalled":
            updated = self._cas_update(
                binding,
                expected_generation=generation,
                values={
                    "active_package_id": package.package_id,
                    "previous_package_id": previous_package or None,
                    "status": "active",
                    "pinned": bool(pin),
                    "trusted": True,
                    "updated_by": actor,
                },
            )
            self._append_event(
                updated,
                event_kind="reinstalled",
                generation=updated.generation,
                previous_package_id=previous_package,
                current_package_id=package.package_id,
                previous_pinned=previous_pinned,
                current_pinned=bool(pin),
                actor_id=actor,
            )
            return self._snapshot(updated)
        updated = self._cas_update(
            binding,
            expected_generation=generation,
            values={"updated_by": actor},
        )
        self._append_event(
            updated,
            event_kind="version_added",
            generation=updated.generation,
            previous_package_id=previous_active,
            current_package_id=package.package_id,
            previous_pinned=previous_pinned,
            current_pinned=previous_pinned,
            actor_id=actor,
        )
        return self._snapshot(updated)

    def upgrade(
        self,
        target: SkillScopeTarget,
        skill_name: str,
        target_version: str,
        *,
        expected_generation: int,
        actor_id: str,
    ) -> SkillBindingSnapshot:
        self._require_managed_target(target)
        name = normalize_skill_name(skill_name)
        version = normalize_semver(target_version)
        actor = _actor(actor_id)
        binding = self._binding(target, name)
        if binding is None or binding.status != "active":
            raise SkillNotFoundError("Skill binding 不存在或未安装")
        generation = self._require_generation(binding, expected_generation)
        if binding.pinned:
            raise SkillLifecycleError("Skill 已 pin；先显式解除 pin 再升级")
        current = self.package_by_id(binding.active_package_id or "")
        target_package = self._package(target, name, version)
        if current is None or target_package is None:
            raise SkillNotFoundError("Skill 当前版本或目标版本不存在")
        current = self._require_package_projection(
            current,
            target=target,
            skill_name=name,
        )
        target_package = self._require_package_projection(
            target_package,
            target=target,
            skill_name=name,
        )
        if semver_key(version) <= semver_key(str(current.version)):
            raise SkillLifecycleError("upgrade 目标必须高于当前 SemVer")
        updated = self._cas_update(
            binding,
            expected_generation=generation,
            values={
                "active_package_id": target_package.package_id,
                "previous_package_id": current.package_id,
                "updated_by": actor,
            },
        )
        self._append_event(
            updated,
            event_kind="upgraded",
            generation=updated.generation,
            previous_package_id=current.package_id,
            current_package_id=target_package.package_id,
            previous_pinned=False,
            current_pinned=False,
            actor_id=actor,
        )
        return self._snapshot(updated)

    def rollback(
        self,
        target: SkillScopeTarget,
        skill_name: str,
        *,
        expected_generation: int,
        actor_id: str,
    ) -> SkillBindingSnapshot:
        self._require_managed_target(target)
        name = normalize_skill_name(skill_name)
        actor = _actor(actor_id)
        binding = self._binding(target, name)
        if binding is None or binding.status != "active":
            raise SkillNotFoundError("Skill binding 不存在或未安装")
        generation = self._require_generation(binding, expected_generation)
        current_id = str(binding.active_package_id or "")
        previous_id = str(binding.previous_package_id or "")
        current = self.package_by_id(current_id)
        previous = self.package_by_id(previous_id)
        if not previous_id or previous is None:
            raise SkillLifecycleError("Skill 没有可回滚的上一版本")
        self._require_package_projection(
            current,
            target=target,
            skill_name=name,
        )
        previous = self._require_package_projection(
            previous,
            target=target,
            skill_name=name,
        )
        pinned = bool(binding.pinned)
        updated = self._cas_update(
            binding,
            expected_generation=generation,
            values={
                "active_package_id": previous.package_id,
                "previous_package_id": current_id,
                "updated_by": actor,
            },
        )
        self._append_event(
            updated,
            event_kind="rolled_back",
            generation=updated.generation,
            previous_package_id=current_id,
            current_package_id=previous_id,
            previous_pinned=pinned,
            current_pinned=pinned,
            actor_id=actor,
        )
        return self._snapshot(updated)

    def set_pinned(
        self,
        target: SkillScopeTarget,
        skill_name: str,
        *,
        pinned: bool,
        expected_generation: int,
        actor_id: str,
    ) -> SkillBindingSnapshot:
        self._require_managed_target(target)
        if type(pinned) is not bool:
            raise SkillContractError("pinned 必须是 bool")
        name = normalize_skill_name(skill_name)
        actor = _actor(actor_id)
        binding = self._binding(target, name)
        if binding is None or binding.status != "active":
            raise SkillNotFoundError("Skill binding 不存在或未安装")
        if bool(binding.pinned) is pinned:
            return self._snapshot(binding)
        generation = self._require_generation(binding, expected_generation)
        active_package_id = str(binding.active_package_id or "")
        updated = self._cas_update(
            binding,
            expected_generation=generation,
            values={"pinned": pinned, "updated_by": actor},
        )
        self._append_event(
            updated,
            event_kind="pinned" if pinned else "unpinned",
            generation=updated.generation,
            previous_package_id=active_package_id,
            current_package_id=active_package_id,
            previous_pinned=not pinned,
            current_pinned=pinned,
            actor_id=actor,
        )
        return self._snapshot(updated)

    def uninstall(
        self,
        target: SkillScopeTarget,
        skill_name: str,
        *,
        expected_generation: int,
        actor_id: str,
    ) -> SkillBindingSnapshot:
        self._require_managed_target(target)
        name = normalize_skill_name(skill_name)
        actor = _actor(actor_id)
        binding = self._binding(target, name)
        if binding is None:
            raise SkillNotFoundError("Skill binding 不存在")
        if binding.status == "uninstalled":
            return self._snapshot(binding)
        generation = self._require_generation(binding, expected_generation)
        current_id = str(binding.active_package_id or "")
        pinned = bool(binding.pinned)
        updated = self._cas_update(
            binding,
            expected_generation=generation,
            values={
                "active_package_id": None,
                "previous_package_id": current_id,
                "status": "uninstalled",
                "updated_by": actor,
            },
        )
        self._append_event(
            updated,
            event_kind="uninstalled",
            generation=updated.generation,
            previous_package_id=current_id,
            current_package_id="",
            previous_pinned=pinned,
            current_pinned=pinned,
            actor_id=actor,
        )
        return self._snapshot(updated)

    def list_bindings(
        self,
        *,
        target: SkillScopeTarget | None = None,
    ) -> tuple[SkillBindingSnapshot, ...]:
        statement = select(SkillBindingRow)
        if target is not None:
            statement = statement.where(
                SkillBindingRow.scope == target.scope.value,
                SkillBindingRow.scope_key == target.scope_key,
            )
        rows = self._db.execute(
            statement.order_by(
                SkillBindingRow.scope,
                SkillBindingRow.scope_key,
                SkillBindingRow.skill_name,
            )
        ).scalars()
        return tuple(self._snapshot(row) for row in rows)

    def active_packages(
        self,
        targets: tuple[SkillScopeTarget, ...],
    ) -> tuple[SkillPackageRow, ...]:
        result: list[SkillPackageRow] = []
        for target in targets:
            if target.scope is SkillScope.BUILTIN:
                continue
            rows = self._db.execute(
                select(SkillBindingRow).where(
                    SkillBindingRow.scope == target.scope.value,
                    SkillBindingRow.scope_key == target.scope_key,
                    SkillBindingRow.status == "active",
                    SkillBindingRow.trusted.is_(True),
                )
            ).scalars()
            for binding in rows:
                package = self.package_by_id(binding.active_package_id or "")
                package = self._require_package_projection(
                    package,
                    target=target,
                    skill_name=normalize_skill_name(binding.skill_name),
                )
                result.append(package)
        return tuple(result)

    def list_versions(
        self,
        *,
        target: SkillScopeTarget | None = None,
        skill_name: str = "",
    ) -> tuple[SkillVersionSnapshot, ...]:
        statement = select(SkillPackageRow).where(
            SkillPackageRow.source_kind == "managed"
        )
        if target is not None:
            statement = statement.where(
                SkillPackageRow.scope == target.scope.value,
                SkillPackageRow.scope_key == target.scope_key,
            )
        if skill_name:
            statement = statement.where(
                SkillPackageRow.skill_name == normalize_skill_name(skill_name)
            )
        rows = self._db.execute(
            statement.order_by(
                SkillPackageRow.scope,
                SkillPackageRow.scope_key,
                SkillPackageRow.skill_name,
                SkillPackageRow.created_at,
                SkillPackageRow.package_id,
            )
        ).scalars()
        snapshots: list[SkillVersionSnapshot] = []
        for row in rows:
            document = self._parsed_package_document(row)
            snapshots.append(SkillVersionSnapshot(
                package_id=str(row.package_id),
                target=SkillScopeTarget(row.scope, row.scope_key),
                skill_name=str(row.skill_name),
                version=str(row.version),
                description=str(row.description),
                bundle_sha256=str(row.bundle_sha256),
                source_kind=str(row.source_kind),
                source_label=str(row.source_label),
                trusted=bool(row.trusted),
                file_count=int(row.file_count),
                bundle_size=int(row.bundle_size),
                capability_tags=document.capability_tags,
                applies_to=document.applies_to,
                allowed_tools=document.allowed_tools,
                dependencies=document.dependencies,
                required_permissions=document.required_permissions,
                body_prompt_tokens=document.body_prompt_tokens,
                catalog_prompt_tokens=document.catalog_prompt_tokens,
            ))
        return tuple(snapshots)

    @staticmethod
    def _parsed_package_document(package: SkillPackageRow) -> ParsedSkillBundle:
        """只解析 SKILL.md 元数据与正文；资源摘要仍以不可变 package 为准。"""

        bundle = parse_skill_bundle(
            bytes(package.skill_md),
            expected_name=str(package.skill_name),
        )
        if (
            bundle.skill_md_sha256 != str(package.skill_md_sha256)
            or bundle.version != str(package.version)
            or bundle.description != str(package.description)
        ):
            raise SkillVersionConflictError("Skill 不可变文档投影不一致")
        return bundle

    def lock_entry_from_package(
        self,
        package: SkillPackageRow,
    ) -> RuntimeSkillLockEntry:
        document = self._parsed_package_document(package)
        return RuntimeSkillLockEntry(
            package_id=package.package_id,
            scope=package.scope,
            name=package.skill_name,
            version=package.version,
            description=package.description,
            license_text=package.license_text,
            compatibility=package.compatibility,
            content_sha256=package.skill_md_sha256,
            bundle_sha256=package.bundle_sha256,
            allowed_tools=_json_tuple(
                package.allowed_tools_json,
                "allowed_tools",
            ),
            dependencies=_json_tuple(
                package.dependencies_json,
                "dependencies",
            ),
            required_permissions=_json_tuple(
                package.required_permissions_json,
                "required_permissions",
            ),
            capability_tags=document.capability_tags,
            applies_to=document.applies_to,
            body_prompt_tokens=document.body_prompt_tokens,
            catalog_prompt_tokens=document.catalog_prompt_tokens,
            source_kind=package.source_kind,
        )

    def load_managed(
        self,
        entry: RuntimeSkillLockEntry,
        *,
        visible_targets: tuple[SkillScopeTarget, ...],
        resource_path: str = "",
    ) -> LoadedSkillContent:
        if entry.source_kind != "managed":
            raise SkillLifecycleError("请求的不是 managed Skill")
        package = self.package_by_id(entry.package_id)
        visible = {(item.scope.value, item.scope_key) for item in visible_targets}
        if package is None or (package.scope, package.scope_key) not in visible:
            raise SkillNotFoundError("Skill 不存在或当前 owner 不可见")
        package = self._require_package_projection(
            package,
            target=SkillScopeTarget(package.scope, package.scope_key),
            skill_name=entry.name,
        )
        actual_entry = self.lock_entry_from_package(package)
        if actual_entry != entry:
            raise SkillVersionConflictError("Skill lock 与不可变版本不一致")
        file_rows = tuple(
            self._db.execute(
                select(SkillPackageFileRow)
                .where(SkillPackageFileRow.package_id == package.package_id)
                .order_by(SkillPackageFileRow.relative_path)
            ).scalars()
        )
        bundle = parse_skill_bundle(
            bytes(package.skill_md),
            files=tuple(
                SkillBundleFile(
                    relative_path=row.relative_path,
                    content=bytes(row.content),
                    media_type=row.media_type,
                )
                for row in file_rows
            ),
            expected_name=package.skill_name,
        )
        if (
            bundle.bundle_sha256 != package.bundle_sha256
            or bundle.skill_md_sha256 != package.skill_md_sha256
            or bundle.version != package.version
        ):
            raise SkillVersionConflictError("Skill 不可变包完整性校验失败")
        if not resource_path:
            return LoadedSkillContent(
                entry=entry,
                body=bundle.body,
                resource_paths=bundle.resource_paths,
            )
        normalized_resource = SkillBundleFile(
            resource_path,
            b"validation-placeholder",
        ).relative_path
        row = next(
            (
                item
                for item in file_rows
                if item.relative_path == normalized_resource
            ),
            None,
        )
        if row is None:
            raise SkillNotFoundError("Skill 资源不存在")
        content = bytes(row.content)
        if (
            hashlib.sha256(content).hexdigest() != row.content_sha256
            or len(content) != int(row.size_bytes)
        ):
            raise SkillVersionConflictError("Skill 资源完整性校验失败")
        return LoadedSkillContent(
            entry=entry,
            body=bundle.body,
            resource_paths=bundle.resource_paths,
            resource_path=normalized_resource,
            resource_media_type=str(row.media_type),
            resource_content=content,
        )


__all__ = [
    "LoadedSkillContent",
    "SkillBindingSnapshot",
    "SkillLifecycleError",
    "SkillLifecycleService",
    "SkillNotFoundError",
    "SkillVersionConflictError",
    "SkillVersionSnapshot",
]
