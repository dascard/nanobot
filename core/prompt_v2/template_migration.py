"""Prompt Runtime 模板的显式计划、应用与可恢复事务。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from collections.abc import Callable
from typing import Any, Literal

from core.prompt_v2.flow_storage import (
    FlowStorageError,
    assert_no_symlink_components,
    atomic_remove_regular_file,
    atomic_replace_bytes,
    ensure_directory_without_symlinks,
    fsync_directory,
    read_regular_bytes,
)
from core.prompt_v2.template_baseline import (
    TemplateBaselineStore,
    serialize_manifest,
    sha256_bytes,
    template_version,
)


ResolutionStrategy = Literal[
    "adopt-in-sync",
    "keep-runtime",
    "use-default",
    "merged-file",
]

_PLAN_SCHEMA_VERSION = 1
_JOURNAL_SCHEMA_VERSION = 1
_ID_RE = re.compile(r"^[0-9a-f]{64}$")


class TemplateMigrationError(ValueError):
    """模板迁移无法安全计划、应用、恢复或回滚。"""


class TemplateMigrationConflictError(TemplateMigrationError):
    """模板存在需要管理员显式决策的内容冲突。"""


class TemplateMigrationStalePlanError(TemplateMigrationError):
    """计划输入在 apply 前发生变化。"""


class TemplateMigrationIntegrityError(TemplateMigrationError):
    """迁移状态、manifest 或 content-addressed blob 已损坏。"""


class TemplateMigrationRecoveryConflictError(TemplateMigrationConflictError):
    """崩溃后的当前字节不再属于事务 before/after 集合。"""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _payload_id(value: dict[str, Any]) -> str:
    return sha256_bytes(_json_bytes(value))


def _read_regular_file(path: Path, *, missing_ok: bool = False) -> bytes | None:
    try:
        return read_regular_bytes(path, missing_ok=missing_ok)
    except FlowStorageError as exc:
        raise TemplateMigrationIntegrityError(str(exc)) from exc


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = _read_regular_file(path)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise TemplateMigrationIntegrityError(f"迁移状态文件不可读: {path}") from exc
    try:
        payload = json.loads((raw or b"").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateMigrationIntegrityError(f"迁移状态文件不是有效 JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise TemplateMigrationIntegrityError(f"迁移状态文件顶层必须是对象: {path}")
    return payload


def _write_immutable(path: Path, payload: bytes) -> None:
    parent = ensure_directory_without_symlinks(path.parent)
    assert_no_symlink_components(path)
    existing = _read_regular_file(path, missing_ok=True)
    if existing is not None:
        if existing != payload:
            raise TemplateMigrationIntegrityError(f"不可变迁移记录发生冲突: {path.name}")
        return
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=parent,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path, follow_symlinks=False)
        except FileExistsError:
            existing = _read_regular_file(path)
            if existing != payload:
                raise TemplateMigrationIntegrityError(
                    f"不可变迁移记录发生冲突: {path.name}"
                )
        fsync_directory(parent)
    finally:
        temp_path.unlink(missing_ok=True)


def _unlink_regular(path: Path) -> None:
    atomic_remove_regular_file(path)


class TemplateMigrationService:
    """在单一状态根内管理模板迁移计划和跨文件 journal。"""

    def __init__(
        self,
        *,
        default_dir: Path,
        runtime_dir: Path,
        state_dir: Path,
        failure_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.store = TemplateBaselineStore(
            default_dir=default_dir,
            runtime_dir=runtime_dir,
            state_dir=state_dir,
        )
        self.default_dir = self.store.default_dir
        self.runtime_dir = self.store.runtime_dir
        self.state_dir = self.store.state_dir
        self._failure_injector = failure_injector

    @classmethod
    def from_environment(cls) -> TemplateMigrationService:
        store = TemplateBaselineStore.from_environment()
        return cls(
            default_dir=store.default_dir,
            runtime_dir=store.runtime_dir,
            state_dir=store.state_dir,
        )

    @property
    def plans_dir(self) -> Path:
        return self.state_dir / "plans"

    @property
    def journal_path(self) -> Path:
        return self.state_dir / "journals" / "pending.json"

    @property
    def operations_dir(self) -> Path:
        return self.state_dir / "operations"

    def _checkpoint(self, point: str) -> None:
        if self._failure_injector is not None:
            self._failure_injector(point)

    def _pending_journal_exists(self) -> bool:
        return _read_regular_file(self.journal_path, missing_ok=True) is not None

    def audit(self, template_keys: list[str] | None = None) -> list[dict[str, Any]]:
        keys = template_keys or self.store.list_template_keys()
        return [self.store.audit(key).to_dict() for key in sorted(set(keys))]

    def _manifest_state(self) -> tuple[dict[str, Any], bytes | None, str | None]:
        manifest = self.store.manifest_snapshot()
        raw = self.store.manifest_bytes()
        return manifest, raw, sha256_bytes(raw) if raw is not None else None

    def _lineage_head(self, manifest: dict[str, Any]) -> str | None:
        lineage = manifest.get("lineage") or []
        if not isinstance(lineage, list):
            raise TemplateMigrationIntegrityError("manifest lineage 必须是数组")
        return str(lineage[-1]) if lineage else None

    def _relative_path(self, runtime_path: Path) -> str:
        try:
            return runtime_path.relative_to(self.runtime_dir).as_posix()
        except ValueError as exc:
            raise TemplateMigrationIntegrityError("runtime 路径逃逸模板根目录") from exc

    def _plan_item(
        self,
        *,
        template_key: str,
        target_bytes: bytes,
        strategy: str,
        merged_source_path: Path | None = None,
        merged_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        from core.prompt_v2.template_validation import (
            TemplateContentValidationError,
            validate_template_bytes,
        )

        key, default_path, runtime_path = self.store.template_paths(template_key)
        canonical_bytes = _read_regular_file(default_path)
        runtime_bytes = _read_regular_file(runtime_path, missing_ok=True)
        if canonical_bytes is None:
            raise TemplateMigrationIntegrityError(
                f"模板 {key} 的 canonical 文件缺失"
            )
        report = self.store.audit(key)
        runtime_repair = (
            report.drift_status == "invalid"
            and report.invalid_component == "runtime_content"
            and strategy in {"use-default", "merged-file"}
        )
        if report.drift_status == "invalid" and not runtime_repair:
            raise TemplateMigrationIntegrityError(
                report.invalid_reason or f"模板 {key} 基线状态 invalid"
            )
        if report.baseline_sha256 is not None:
            self.store.read_baseline_bytes(key)
        canonical_sha256 = sha256_bytes(canonical_bytes)
        try:
            validate_template_bytes(key, target_bytes)
        except TemplateContentValidationError as exc:
            raise TemplateMigrationIntegrityError(str(exc)) from exc
        target_sha256 = self.store.install_blob_once(target_bytes)
        self.store.install_blob_once(canonical_bytes)
        return {
            "template_key": key,
            "relative_path": self._relative_path(runtime_path),
            "strategy": strategy,
            "baseline_sha256": report.baseline_sha256,
            "runtime_exists": runtime_bytes is not None,
            "runtime_sha256": (
                sha256_bytes(runtime_bytes) if runtime_bytes is not None else None
            ),
            "canonical_sha256": canonical_sha256,
            "canonical_version": template_version(
                canonical_bytes,
                fallback=canonical_sha256[:12],
            ),
            "target_sha256": target_sha256,
            "merged_source_path": (
                str(merged_source_path) if merged_source_path is not None else None
            ),
            "merged_source_sha256": merged_source_sha256,
        }

    def _build_plan(
        self,
        *,
        operation_type: str,
        modified_by: str,
        manifest: dict[str, Any],
        manifest_sha256: str | None,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        actor = str(modified_by or "").strip()
        if not actor:
            raise TemplateMigrationError("modified_by 不能为空")
        payload = {
            "schema_version": _PLAN_SCHEMA_VERSION,
            "operation_type": operation_type,
            "modified_by": actor,
            "default_root": str(self.default_dir),
            "runtime_root": str(self.runtime_dir),
            "state_root": str(self.state_dir),
            "manifest_revision": manifest["revision"],
            "manifest_sha256": manifest_sha256,
            "lineage_head": self._lineage_head(manifest),
            "items": sorted(items, key=lambda item: item["template_key"]),
        }
        plan_id = _payload_id(payload)
        plan = {"plan_id": plan_id, **payload}
        _write_immutable(self.plans_dir / f"{plan_id}.json", _json_bytes(plan))
        return copy.deepcopy(plan)

    def plan(
        self,
        *,
        template_keys: list[str] | None = None,
        modified_by: str,
    ) -> dict[str, Any]:
        keys = sorted(set(template_keys or self.store.list_template_keys()))
        if not keys:
            raise TemplateMigrationConflictError("没有可计划的模板")
        with self.store.transaction_lock():
            manifest, _raw, manifest_sha256 = self._manifest_state()
            candidates: list[tuple[str, bytes]] = []
            for key in keys:
                report = self.store.audit(key)
                if report.drift_status == "invalid":
                    raise TemplateMigrationIntegrityError(
                        report.invalid_reason or f"模板 {key} 状态 invalid"
                    )
                if report.drift_status == "in_sync":
                    continue
                if report.drift_status != "upgrade_available":
                    raise TemplateMigrationConflictError(
                        f"模板 {key} 当前状态为 {report.drift_status}，不能自动 plan"
                    )
                _resolved_key, default_path, _runtime_path = self.store.template_paths(key)
                canonical_bytes = _read_regular_file(default_path)
                if canonical_bytes is None:
                    raise TemplateMigrationIntegrityError(
                        f"模板 {key} canonical 文件缺失"
                    )
                candidates.append((key, canonical_bytes))
            if not candidates:
                raise TemplateMigrationConflictError("没有 upgrade_available 模板")
            items = [
                self._plan_item(
                    template_key=key,
                    target_bytes=canonical_bytes,
                    strategy="upgrade",
                )
                for key, canonical_bytes in candidates
            ]
            return self._build_plan(
                operation_type="upgrade",
                modified_by=modified_by,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                items=items,
            )

    def resolve(
        self,
        *,
        template_key: str,
        strategy: ResolutionStrategy,
        modified_by: str,
        merged_file: Path | None = None,
    ) -> dict[str, Any]:
        if strategy not in {
            "adopt-in-sync",
            "keep-runtime",
            "use-default",
            "merged-file",
        }:
            raise TemplateMigrationError(f"未知 resolve strategy: {strategy}")
        with self.store.transaction_lock():
            manifest, _raw, manifest_sha256 = self._manifest_state()
            key, default_path, runtime_path = self.store.template_paths(template_key)
            canonical_bytes = _read_regular_file(default_path)
            runtime_bytes = _read_regular_file(runtime_path, missing_ok=True)
            if canonical_bytes is None:
                raise TemplateMigrationIntegrityError(
                    f"模板 {key} canonical 文件缺失"
                )
            report = self.store.audit(key)
            if report.drift_status == "invalid":
                runtime_repair = (
                    report.invalid_component == "runtime_content"
                    and strategy in {"use-default", "merged-file"}
                )
                if not runtime_repair and report.invalid_component == "runtime_content":
                    raise TemplateMigrationIntegrityError(
                        f"模板 {key} runtime 内容损坏，策略 {strategy} 不能复用该内容"
                    )
                if not runtime_repair:
                    raise TemplateMigrationIntegrityError(
                        report.invalid_reason or f"模板 {key} 状态 invalid"
                    )
            merged_source_path: Path | None = None
            merged_source_sha256: str | None = None
            if strategy == "adopt-in-sync":
                if (
                    report.drift_status != "untracked_legacy"
                    or runtime_bytes is None
                    or runtime_bytes != canonical_bytes
                ):
                    raise TemplateMigrationConflictError(
                        f"模板 {key} 不满足 adopt-in-sync 条件"
                    )
                target_bytes = runtime_bytes
            elif strategy == "keep-runtime":
                if runtime_bytes is None:
                    raise TemplateMigrationConflictError(
                        f"模板 {key} 缺少 runtime，不能 keep-runtime"
                    )
                target_bytes = runtime_bytes
            elif strategy == "use-default":
                target_bytes = canonical_bytes
            else:
                if merged_file is None:
                    raise TemplateMigrationError("merged-file strategy 必须提供 merged_file")
                merged_source_path = Path(os.path.abspath(os.fspath(merged_file)))
                merged_bytes = _read_regular_file(merged_source_path)
                if merged_bytes is None:
                    raise TemplateMigrationIntegrityError("merged 文件缺失")
                merged_source_sha256 = sha256_bytes(merged_bytes)
                target_bytes = merged_bytes
            item = self._plan_item(
                template_key=key,
                target_bytes=target_bytes,
                strategy=strategy,
                merged_source_path=merged_source_path,
                merged_source_sha256=merged_source_sha256,
            )
            return self._build_plan(
                operation_type=f"resolve:{strategy}",
                modified_by=modified_by,
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                items=[item],
            )

    def _load_plan(self, plan_id: str) -> dict[str, Any]:
        value = str(plan_id or "").strip()
        if not _ID_RE.fullmatch(value):
            raise TemplateMigrationError("plan_id 必须是完整 SHA-256")
        plan = _read_json_file(self.plans_dir / f"{value}.json")
        stored_id = str(plan.pop("plan_id", ""))
        if stored_id != value or _payload_id(plan) != value:
            raise TemplateMigrationIntegrityError("迁移 plan_id 与计划内容不一致")
        return {"plan_id": stored_id, **plan}

    def _validate_plan_inputs(
        self,
        plan: dict[str, Any],
    ) -> tuple[dict[str, Any], bytes | None]:
        if plan.get("schema_version") != _PLAN_SCHEMA_VERSION:
            raise TemplateMigrationIntegrityError("迁移计划 schema_version 不受支持")
        expected_roots = {
            "default_root": str(self.default_dir),
            "runtime_root": str(self.runtime_dir),
            "state_root": str(self.state_dir),
        }
        for field, expected in expected_roots.items():
            if plan.get(field) != expected:
                raise TemplateMigrationStalePlanError(f"计划 {field} 已变化")
        manifest, raw, digest = self._manifest_state()
        if (
            plan.get("manifest_sha256") != digest
            or plan.get("manifest_revision") != manifest.get("revision")
            or plan.get("lineage_head") != self._lineage_head(manifest)
        ):
            raise TemplateMigrationStalePlanError("manifest 已变化，拒绝 stale plan")
        items = plan.get("items")
        if not isinstance(items, list) or not items:
            raise TemplateMigrationIntegrityError("迁移计划 items 不能为空")
        for item in items:
            if not isinstance(item, dict):
                raise TemplateMigrationIntegrityError("迁移计划 item 必须是对象")
            key, default_path, runtime_path = self.store.template_paths(
                str(item.get("template_key") or "")
            )
            if item.get("relative_path") != self._relative_path(runtime_path):
                raise TemplateMigrationIntegrityError(f"模板 {key} relative_path 非法")
            canonical_bytes = _read_regular_file(default_path)
            canonical_sha256 = (
                sha256_bytes(canonical_bytes) if canonical_bytes is not None else None
            )
            if canonical_sha256 != item.get("canonical_sha256"):
                raise TemplateMigrationStalePlanError(
                    f"模板 {key} canonical 已变化"
                )
            runtime_bytes = _read_regular_file(runtime_path, missing_ok=True)
            runtime_sha256 = (
                sha256_bytes(runtime_bytes) if runtime_bytes is not None else None
            )
            if (
                (runtime_bytes is not None) != bool(item.get("runtime_exists"))
                or runtime_sha256 != item.get("runtime_sha256")
            ):
                raise TemplateMigrationStalePlanError(f"模板 {key} runtime 已变化")
            baseline_sha256 = item.get("baseline_sha256")
            if baseline_sha256 is not None:
                baseline = self.store.read_baseline_bytes(key)
                if sha256_bytes(baseline) != baseline_sha256:
                    raise TemplateMigrationStalePlanError(
                        f"模板 {key} baseline 已变化"
                    )
            target_sha256 = str(item.get("target_sha256") or "")
            target_bytes = self.store.read_blob_by_sha256(target_sha256)
            from core.prompt_v2.template_validation import (
                TemplateContentValidationError,
                validate_template_bytes,
            )

            try:
                validate_template_bytes(key, target_bytes)
            except TemplateContentValidationError as exc:
                raise TemplateMigrationIntegrityError(str(exc)) from exc
            merged_path = item.get("merged_source_path")
            if merged_path:
                merged_bytes = _read_regular_file(Path(str(merged_path)))
                if sha256_bytes(merged_bytes or b"") != item.get("merged_source_sha256"):
                    raise TemplateMigrationStalePlanError(
                        f"模板 {key} merged 文件已变化"
                    )
        return manifest, raw

    def _write_journal(self, journal: dict[str, Any]) -> None:
        atomic_replace_bytes(self.journal_path, _json_bytes(journal))

    def _runtime_path_from_relative(self, relative_path: str) -> Path:
        value = str(relative_path or "").strip()
        candidate = Path(value)
        if (
            not value
            or candidate.is_absolute()
            or "\\" in value
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise TemplateMigrationIntegrityError("journal relative_path 非法")
        path = self.runtime_dir / candidate
        try:
            path.relative_to(self.runtime_dir)
        except ValueError as exc:
            raise TemplateMigrationIntegrityError("journal runtime 路径逃逸") from exc
        return path

    @staticmethod
    def _artifact_state(path: Path) -> tuple[bool, str | None]:
        content = _read_regular_file(path, missing_ok=True)
        return content is not None, sha256_bytes(content) if content is not None else None

    @staticmethod
    def _matches_state(
        *,
        exists: bool,
        digest: str | None,
        expected_exists: bool,
        expected_digest: str | None,
    ) -> bool:
        return exists == expected_exists and digest == expected_digest

    def _verified_journal(self) -> dict[str, Any]:
        journal = _read_json_file(self.journal_path)
        if journal.get("schema_version") != _JOURNAL_SCHEMA_VERSION:
            raise TemplateMigrationIntegrityError("journal schema_version 不受支持")
        journal_state = journal.get("state")
        if journal_state not in {
            "prepared",
            "files_installed",
            "state_committed",
        }:
            raise TemplateMigrationIntegrityError("journal state 非法")
        operation_id = str(journal.get("operation_id") or "")
        if not _ID_RE.fullmatch(operation_id):
            raise TemplateMigrationIntegrityError("journal operation_id 非法")
        if (
            journal.get("runtime_root") != str(self.runtime_dir)
            or journal.get("state_root") != str(self.state_dir)
        ):
            raise TemplateMigrationIntegrityError("journal 根目录与当前配置不一致")
        items = journal.get("items")
        if not isinstance(items, list) or not items:
            raise TemplateMigrationIntegrityError("journal items 不能为空")

        for item in items:
            if not isinstance(item, dict):
                raise TemplateMigrationIntegrityError("journal item 必须是对象")
            runtime_path = self._runtime_path_from_relative(
                str(item.get("relative_path") or "")
            )
            before_exists = bool(item.get("before_exists"))
            after_exists = bool(item.get("after_exists"))
            before_sha256 = item.get("before_sha256")
            after_sha256 = item.get("after_sha256")
            installed = item.get("installed")
            if type(installed) is not bool:
                raise TemplateMigrationIntegrityError(
                    "journal item installed 必须是布尔值"
                )
            if journal_state in {"files_installed", "state_committed"} and not installed:
                raise TemplateMigrationIntegrityError(
                    "journal state 与 item 安装进度不一致"
                )
            if before_exists:
                before = self.store.read_blob_by_sha256(
                    str(item.get("before_blob_sha256") or "")
                )
                if sha256_bytes(before) != before_sha256:
                    raise TemplateMigrationIntegrityError(
                        f"journal before blob 与摘要不一致: {item.get('template_key')}"
                    )
            elif before_sha256 is not None or item.get("before_blob_sha256") is not None:
                raise TemplateMigrationIntegrityError("journal absent before 状态不一致")
            if after_exists:
                after = self.store.read_blob_by_sha256(
                    str(item.get("after_blob_sha256") or "")
                )
                if sha256_bytes(after) != after_sha256:
                    raise TemplateMigrationIntegrityError(
                        f"journal after blob 与摘要不一致: {item.get('template_key')}"
                    )
            elif after_sha256 is not None or item.get("after_blob_sha256") is not None:
                raise TemplateMigrationIntegrityError("journal absent after 状态不一致")
            current_exists, current_sha256 = self._artifact_state(runtime_path)
            matches_before = self._matches_state(
                exists=current_exists,
                digest=current_sha256,
                expected_exists=before_exists,
                expected_digest=before_sha256,
            )
            matches_after = self._matches_state(
                exists=current_exists,
                digest=current_sha256,
                expected_exists=after_exists,
                expected_digest=after_sha256,
            )
            if installed and not matches_after:
                raise TemplateMigrationRecoveryConflictError(
                    f"模板 {item.get('template_key')} 已安装后出现事务外字节，必须人工恢复"
                )
            if not installed and not matches_before and not matches_after:
                raise TemplateMigrationRecoveryConflictError(
                    f"模板 {item.get('template_key')} 出现事务外字节，必须人工恢复"
                )

        for prefix in ("before", "after"):
            expected_exists = bool(journal.get(f"manifest_{prefix}_exists"))
            expected_sha256 = journal.get(f"manifest_{prefix}_sha256")
            blob_sha256 = journal.get(f"manifest_{prefix}_blob_sha256")
            if expected_exists:
                raw = self.store.read_blob_by_sha256(str(blob_sha256 or ""))
                if sha256_bytes(raw) != expected_sha256:
                    raise TemplateMigrationIntegrityError(
                        f"journal manifest {prefix} blob 与摘要不一致"
                    )
            elif expected_sha256 is not None or blob_sha256 is not None:
                raise TemplateMigrationIntegrityError(
                    f"journal manifest {prefix} absent 状态不一致"
                )
        manifest_exists, manifest_sha256 = self._artifact_state(
            self.store.manifest_path
        )
        matches_manifest_before = self._matches_state(
            exists=manifest_exists,
            digest=manifest_sha256,
            expected_exists=bool(journal.get("manifest_before_exists")),
            expected_digest=journal.get("manifest_before_sha256"),
        )
        matches_manifest_after = self._matches_state(
            exists=manifest_exists,
            digest=manifest_sha256,
            expected_exists=bool(journal.get("manifest_after_exists")),
            expected_digest=journal.get("manifest_after_sha256"),
        )
        manifest_state_valid = (
            matches_manifest_before
            if journal_state == "prepared"
            else matches_manifest_after
            if journal_state == "state_committed"
            else matches_manifest_before or matches_manifest_after
        )
        if not manifest_state_valid:
            raise TemplateMigrationRecoveryConflictError(
                "manifest 出现事务外字节，必须人工恢复"
            )
        return journal

    def _archive_and_close_journal(self, journal: dict[str, Any]) -> None:
        operation_id = str(journal["operation_id"])
        operation = {
            **journal,
            "status": str(journal.get("operation_status") or "committed"),
        }
        _write_immutable(
            self.operations_dir / f"{operation_id}.json",
            _json_bytes(operation),
        )
        _unlink_regular(self.journal_path)

    def _install_journal_after(
        self,
        journal: dict[str, Any],
        *,
        inject_failures: bool,
    ) -> None:
        for item in journal["items"]:
            runtime_path = self._runtime_path_from_relative(item["relative_path"])
            current_exists, current_sha256 = self._artifact_state(runtime_path)
            matches_before = self._matches_state(
                exists=current_exists,
                digest=current_sha256,
                expected_exists=bool(item["before_exists"]),
                expected_digest=item["before_sha256"],
            )
            matches_after = self._matches_state(
                exists=current_exists,
                digest=current_sha256,
                expected_exists=bool(item["after_exists"]),
                expected_digest=item["after_sha256"],
            )
            if item["installed"]:
                if not matches_after:
                    raise TemplateMigrationRecoveryConflictError(
                        f"模板 {item.get('template_key')} 已安装后出现事务外字节，必须人工恢复"
                    )
                continue
            if not matches_before and not matches_after:
                raise TemplateMigrationRecoveryConflictError(
                    f"模板 {item.get('template_key')} 出现事务外字节，必须人工恢复"
                )
            if not matches_after:
                if item["after_exists"]:
                    target = self.store.read_blob_by_sha256(
                        item["after_blob_sha256"]
                    )
                    atomic_replace_bytes(runtime_path, target)
                else:
                    _unlink_regular(runtime_path)
                installed_exists, installed_sha256 = self._artifact_state(runtime_path)
                if not self._matches_state(
                    exists=installed_exists,
                    digest=installed_sha256,
                    expected_exists=bool(item["after_exists"]),
                    expected_digest=item["after_sha256"],
                ):
                    raise TemplateMigrationRecoveryConflictError(
                        f"模板 {item.get('template_key')} 安装结果无法验证，必须人工恢复"
                    )
            item["installed"] = True
            self._write_journal(journal)
            if inject_failures:
                self._checkpoint("after_file_installed")
        if journal["state"] == "prepared":
            journal["state"] = "files_installed"
            self._write_journal(journal)
            if inject_failures:
                self._checkpoint("after_files_installed")

        manifest_exists, manifest_sha256 = self._artifact_state(
            self.store.manifest_path
        )
        manifest_after_exists = bool(journal["manifest_after_exists"])
        manifest_after_sha256 = journal["manifest_after_sha256"]
        matches_manifest_after = self._matches_state(
            exists=manifest_exists,
            digest=manifest_sha256,
            expected_exists=manifest_after_exists,
            expected_digest=manifest_after_sha256,
        )
        if journal["state"] == "state_committed":
            if not matches_manifest_after:
                raise TemplateMigrationRecoveryConflictError(
                    "manifest 提交后出现事务外字节，必须人工恢复"
                )
            self._archive_and_close_journal(journal)
            return
        matches_manifest_before = self._matches_state(
            exists=manifest_exists,
            digest=manifest_sha256,
            expected_exists=bool(journal["manifest_before_exists"]),
            expected_digest=journal["manifest_before_sha256"],
        )
        if not matches_manifest_before and not matches_manifest_after:
            raise TemplateMigrationRecoveryConflictError(
                "manifest 出现事务外字节，必须人工恢复"
            )
        if not matches_manifest_after:
            if manifest_after_exists:
                raw = self.store.read_blob_by_sha256(
                    journal["manifest_after_blob_sha256"]
                )
                self.store.write_manifest_bytes(raw)
            else:
                _unlink_regular(self.store.manifest_path)
            installed_exists, installed_sha256 = self._artifact_state(
                self.store.manifest_path
            )
            if not self._matches_state(
                exists=installed_exists,
                digest=installed_sha256,
                expected_exists=manifest_after_exists,
                expected_digest=manifest_after_sha256,
            ):
                raise TemplateMigrationRecoveryConflictError(
                    "manifest 安装结果无法验证，必须人工恢复"
                )
        if inject_failures:
            self._checkpoint("after_manifest_committed")
        journal["state"] = "state_committed"
        self._write_journal(journal)
        if inject_failures:
            self._checkpoint("after_state_committed")
        self._archive_and_close_journal(journal)

    @staticmethod
    def _apply_operation_id(plan_id: str, attempt: int) -> str:
        if attempt == 1:
            return sha256_bytes(f"apply:{plan_id}".encode("utf-8"))
        return _payload_id(
            {
                "operation_type": "apply",
                "plan_id": plan_id,
                "attempt": attempt,
            }
        )

    def _apply_operation_history(
        self,
        plan_id: str,
    ) -> tuple[list[dict[str, Any]], int, str]:
        operations: list[dict[str, Any]] = []
        attempt = 1
        while True:
            operation_id = self._apply_operation_id(plan_id, attempt)
            operation_path = self.operations_dir / f"{operation_id}.json"
            if _read_regular_file(operation_path, missing_ok=True) is None:
                return operations, attempt, operation_id
            operation = _read_json_file(operation_path)
            if (
                operation.get("operation_id") != operation_id
                or operation.get("plan_id") != plan_id
                or operation.get("status") != "committed"
            ):
                raise TemplateMigrationIntegrityError(
                    f"apply operation 审计记录无效: {operation_id}"
                )
            operations.append(operation)
            attempt += 1

    def _operation_matches_after(self, operation: dict[str, Any]) -> bool:
        manifest_exists, manifest_sha256 = self._artifact_state(
            self.store.manifest_path
        )
        if not self._matches_state(
            exists=manifest_exists,
            digest=manifest_sha256,
            expected_exists=bool(operation.get("manifest_after_exists")),
            expected_digest=operation.get("manifest_after_sha256"),
        ):
            return False
        items = operation.get("items")
        if not isinstance(items, list) or not items:
            raise TemplateMigrationIntegrityError("apply operation 缺少 items")
        for item in items:
            if not isinstance(item, dict):
                raise TemplateMigrationIntegrityError("apply operation item 必须是对象")
            runtime_path = self._runtime_path_from_relative(
                str(item.get("relative_path") or "")
            )
            current_exists, current_sha256 = self._artifact_state(runtime_path)
            if not self._matches_state(
                exists=current_exists,
                digest=current_sha256,
                expected_exists=bool(item.get("after_exists")),
                expected_digest=item.get("after_sha256"),
            ):
                return False
        return True

    def _build_manifest_after(
        self,
        *,
        manifest_before: dict[str, Any],
        plan: dict[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        manifest = copy.deepcopy(manifest_before)
        for item in plan["items"]:
            key = item["template_key"]
            manifest["templates"][key] = {
                "template_key": key,
                "baseline_version": item["canonical_version"],
                "baseline_sha256": item["canonical_sha256"],
                "baseline_blob_sha256": item["canonical_sha256"],
                "canonical_sha256": item["canonical_sha256"],
                "runtime_sha256": item["target_sha256"],
                "modified_by": plan["modified_by"],
                "modification_source": plan["operation_type"],
                "last_migration_id": operation_id,
            }
        manifest["revision"] += 1
        manifest["lineage"].append(operation_id)
        return manifest

    def provision_missing(
        self,
        template_key: str,
        *,
        modified_by: str = "startup-provision",
    ) -> bool:
        """仅为真正缺失且尚未接管的 runtime 文件建立可恢复首次基线。"""
        actor = str(modified_by or "").strip()
        if not actor:
            raise TemplateMigrationError("modified_by 不能为空")
        with self.store.transaction_lock():
            if self._pending_journal_exists():
                raise TemplateMigrationIntegrityError(
                    "存在未恢复的模板迁移 journal，拒绝 provision"
                )
            key, default_path, runtime_path = self.store.template_paths(template_key)
            if _read_regular_file(runtime_path, missing_ok=True) is not None:
                return False
            manifest_before, manifest_before_raw, manifest_before_sha256 = (
                self._manifest_state()
            )
            if key in manifest_before["templates"]:
                return False
            canonical_bytes = _read_regular_file(default_path)
            if canonical_bytes is None:
                raise TemplateMigrationIntegrityError(
                    f"模板 {key} canonical 文件缺失"
                )
            from core.prompt_v2.template_validation import (
                TemplateContentValidationError,
                validate_template_bytes,
            )

            try:
                validate_template_bytes(key, canonical_bytes)
            except TemplateContentValidationError as exc:
                raise TemplateMigrationIntegrityError(str(exc)) from exc
            canonical_sha256 = self.store.install_blob_once(canonical_bytes)
            operation_payload = {
                "operation_type": "startup-provision",
                "template_key": key,
                "runtime_root": str(self.runtime_dir),
                "state_root": str(self.state_dir),
                "manifest_revision": manifest_before["revision"],
                "manifest_sha256": manifest_before_sha256,
                "canonical_sha256": canonical_sha256,
                "nonce": secrets.token_hex(32),
            }
            operation_id = _payload_id(operation_payload)
            manifest_after = copy.deepcopy(manifest_before)
            manifest_after["templates"][key] = {
                "template_key": key,
                "baseline_version": template_version(
                    canonical_bytes,
                    fallback=canonical_sha256[:12],
                ),
                "baseline_sha256": canonical_sha256,
                "baseline_blob_sha256": canonical_sha256,
                "canonical_sha256": canonical_sha256,
                "runtime_sha256": canonical_sha256,
                "modified_by": actor,
                "modification_source": "startup-provision",
                "last_migration_id": operation_id,
            }
            manifest_after["revision"] += 1
            manifest_after["lineage"].append(operation_id)
            manifest_after_raw = serialize_manifest(manifest_after)
            manifest_before_blob = (
                self.store.install_blob_once(manifest_before_raw)
                if manifest_before_raw is not None
                else None
            )
            manifest_after_blob = self.store.install_blob_once(manifest_after_raw)
            journal = {
                "schema_version": _JOURNAL_SCHEMA_VERSION,
                "operation_id": operation_id,
                "plan_id": None,
                "operation_type": "startup-provision",
                "operation_status": "committed",
                "modified_by": actor,
                "runtime_root": str(self.runtime_dir),
                "state_root": str(self.state_dir),
                "state": "prepared",
                "manifest_before_exists": manifest_before_raw is not None,
                "manifest_before_sha256": manifest_before_sha256,
                "manifest_before_blob_sha256": manifest_before_blob,
                "manifest_after_exists": True,
                "manifest_after_sha256": sha256_bytes(manifest_after_raw),
                "manifest_after_blob_sha256": manifest_after_blob,
                "items": [
                    {
                        "template_key": key,
                        "relative_path": self._relative_path(runtime_path),
                        "before_exists": False,
                        "before_sha256": None,
                        "before_blob_sha256": None,
                        "after_exists": True,
                        "after_sha256": canonical_sha256,
                        "after_blob_sha256": canonical_sha256,
                        "installed": False,
                    }
                ],
            }
            self._write_journal(journal)
            self._checkpoint("after_journal_prepared")
            self._install_journal_after(journal, inject_failures=True)
            return True

    def apply_runtime_change(
        self,
        template_key: str,
        *,
        target_bytes: bytes | None,
        modified_by: str,
        operation_type: str,
        require_absent: bool = False,
    ) -> dict[str, Any]:
        """通过持久 journal 应用一次 Admin runtime 创建、修改或删除。"""
        actor = str(modified_by or "").strip()
        source = str(operation_type or "").strip()
        if not actor:
            raise TemplateMigrationError("modified_by 不能为空")
        if source not in {
            "admin-create",
            "admin-save",
            "admin-delete",
            "admin-flow-save",
        }:
            raise TemplateMigrationError("Admin 模板操作类型非法")

        with self.store.transaction_lock():
            if self._pending_journal_exists():
                raise TemplateMigrationIntegrityError(
                    "存在未恢复的模板迁移 journal，拒绝 Admin 写入"
                )
            key, default_path, runtime_path = self.store.template_paths(template_key)
            if target_bytes is not None:
                from core.prompt_v2.template_validation import (
                    TemplateContentValidationError,
                    validate_template_bytes,
                )

                try:
                    validate_template_bytes(key, target_bytes)
                except TemplateContentValidationError as exc:
                    raise TemplateMigrationIntegrityError(str(exc)) from exc

            before_bytes = _read_regular_file(runtime_path, missing_ok=True)
            if require_absent and before_bytes is not None:
                raise TemplateMigrationConflictError("运行时模板已存在")
            manifest_before, manifest_before_raw, manifest_before_sha256 = (
                self._manifest_state()
            )
            manifest_after = copy.deepcopy(manifest_before)
            entry = manifest_after["templates"].get(key)
            canonical_bytes = _read_regular_file(default_path, missing_ok=True)
            if entry is not None:
                self.store.read_baseline_bytes(key)
                if canonical_bytes is None:
                    raise TemplateMigrationIntegrityError(
                        f"模板 {key} canonical 文件缺失"
                    )
                from core.prompt_v2.template_validation import (
                    TemplateContentValidationError,
                    validate_template_bytes,
                )

                try:
                    validate_template_bytes(key, canonical_bytes)
                except TemplateContentValidationError as exc:
                    raise TemplateMigrationIntegrityError(str(exc)) from exc

            before_sha256 = (
                sha256_bytes(before_bytes) if before_bytes is not None else None
            )
            after_sha256 = (
                sha256_bytes(target_bytes) if target_bytes is not None else None
            )
            operation_id = _payload_id(
                {
                    "operation_type": source,
                    "template_key": key,
                    "manifest_sha256": manifest_before_sha256,
                    "before_sha256": before_sha256,
                    "after_sha256": after_sha256,
                    "nonce": secrets.token_hex(32),
                }
            )

            if entry is not None:
                entry["canonical_sha256"] = sha256_bytes(canonical_bytes or b"")
                entry["runtime_sha256"] = after_sha256
                entry["modified_by"] = actor
                entry["modification_source"] = source
                entry["last_migration_id"] = operation_id
                manifest_after["revision"] += 1
                manifest_after["lineage"].append(operation_id)
                manifest_after_raw = serialize_manifest(manifest_after)
            else:
                manifest_after_raw = manifest_before_raw

            before_blob = (
                self.store.install_blob_once(before_bytes)
                if before_bytes is not None
                else None
            )
            after_blob = (
                self.store.install_blob_once(target_bytes)
                if target_bytes is not None
                else None
            )
            manifest_before_blob = (
                self.store.install_blob_once(manifest_before_raw)
                if manifest_before_raw is not None
                else None
            )
            manifest_after_blob = (
                self.store.install_blob_once(manifest_after_raw)
                if manifest_after_raw is not None
                else None
            )
            journal = {
                "schema_version": _JOURNAL_SCHEMA_VERSION,
                "operation_id": operation_id,
                "plan_id": None,
                "operation_type": source,
                "operation_status": "committed",
                "modified_by": actor,
                "runtime_root": str(self.runtime_dir),
                "state_root": str(self.state_dir),
                "state": "prepared",
                "manifest_before_exists": manifest_before_raw is not None,
                "manifest_before_sha256": manifest_before_sha256,
                "manifest_before_blob_sha256": manifest_before_blob,
                "manifest_after_exists": manifest_after_raw is not None,
                "manifest_after_sha256": (
                    sha256_bytes(manifest_after_raw)
                    if manifest_after_raw is not None
                    else None
                ),
                "manifest_after_blob_sha256": manifest_after_blob,
                "items": [
                    {
                        "template_key": key,
                        "relative_path": self._relative_path(runtime_path),
                        "before_exists": before_bytes is not None,
                        "before_sha256": before_sha256,
                        "before_blob_sha256": before_blob,
                        "after_exists": target_bytes is not None,
                        "after_sha256": after_sha256,
                        "after_blob_sha256": after_blob,
                        "installed": False,
                    }
                ],
            }
            self._write_journal(journal)
            self._checkpoint("after_journal_prepared")
            self._install_journal_after(journal, inject_failures=True)
            return {
                "status": "applied",
                "operation_id": operation_id,
                "template_key": key,
                "changed": before_bytes != target_bytes,
                "_before_bytes": before_bytes,
            }

    def apply(self, plan_id: str) -> dict[str, Any]:
        with self.store.transaction_lock():
            plan = self._load_plan(plan_id)
            if self._pending_journal_exists():
                journal = self._verified_journal()
                if (
                    journal.get("plan_id") != plan_id
                ):
                    raise TemplateMigrationIntegrityError(
                        "存在属于其他操作的未恢复 journal，拒绝 apply"
                    )
                operation_id = str(journal["operation_id"])
                self._install_journal_after(journal, inject_failures=False)
                return {
                    "status": "recovered",
                    "operation_id": operation_id,
                    "plan_id": plan_id,
                }
            history, apply_attempt, operation_id = self._apply_operation_history(
                plan_id
            )
            matching_operation = next(
                (
                    operation
                    for operation in reversed(history)
                    if self._operation_matches_after(operation)
                ),
                None,
            )
            if matching_operation is not None:
                return {
                    "status": "already_applied",
                    "operation_id": matching_operation["operation_id"],
                    "plan_id": plan_id,
                }
            manifest_before, manifest_before_raw = self._validate_plan_inputs(plan)
            manifest_after = self._build_manifest_after(
                manifest_before=manifest_before,
                plan=plan,
                operation_id=operation_id,
            )
            manifest_after_raw = serialize_manifest(manifest_after)
            manifest_before_blob = (
                self.store.install_blob_once(manifest_before_raw)
                if manifest_before_raw is not None
                else None
            )
            manifest_after_blob = self.store.install_blob_once(manifest_after_raw)
            journal_items: list[dict[str, Any]] = []
            for item in plan["items"]:
                _key, _default_path, runtime_path = self.store.template_paths(
                    item["template_key"]
                )
                before_bytes = _read_regular_file(runtime_path, missing_ok=True)
                before_blob = (
                    self.store.install_blob_once(before_bytes)
                    if before_bytes is not None
                    else None
                )
                journal_items.append(
                    {
                        "template_key": item["template_key"],
                        "relative_path": item["relative_path"],
                        "before_exists": before_bytes is not None,
                        "before_sha256": (
                            sha256_bytes(before_bytes)
                            if before_bytes is not None
                            else None
                        ),
                        "before_blob_sha256": before_blob,
                        "after_exists": True,
                        "after_sha256": item["target_sha256"],
                        "after_blob_sha256": item["target_sha256"],
                        "installed": False,
                    }
                )
            journal = {
                "schema_version": _JOURNAL_SCHEMA_VERSION,
                "operation_id": operation_id,
                "plan_id": plan_id,
                "apply_attempt": apply_attempt,
                "operation_type": plan["operation_type"],
                "operation_status": "committed",
                "modified_by": plan["modified_by"],
                "runtime_root": str(self.runtime_dir),
                "state_root": str(self.state_dir),
                "state": "prepared",
                "manifest_before_exists": manifest_before_raw is not None,
                "manifest_before_sha256": (
                    sha256_bytes(manifest_before_raw)
                    if manifest_before_raw is not None
                    else None
                ),
                "manifest_before_blob_sha256": manifest_before_blob,
                "manifest_after_exists": True,
                "manifest_after_sha256": sha256_bytes(manifest_after_raw),
                "manifest_after_blob_sha256": manifest_after_blob,
                "items": journal_items,
            }
            self._write_journal(journal)
            self._checkpoint("after_journal_prepared")
            self._install_journal_after(journal, inject_failures=True)
            return {
                "status": "applied",
                "operation_id": operation_id,
                "plan_id": plan_id,
            }

    def recover(self) -> dict[str, Any]:
        """完成一个仍可证明 before/after 的 pending journal。"""
        with self.store.transaction_lock():
            if not self._pending_journal_exists():
                return {"status": "clean", "operation_id": None}
            journal = self._verified_journal()
            initial_state = journal["state"]
            operation_id = journal["operation_id"]
            self._install_journal_after(journal, inject_failures=False)
            return {
                "status": (
                    "already_committed"
                    if initial_state == "state_committed"
                    else "recovered"
                ),
                "operation_id": operation_id,
            }

    def rollback(
        self,
        operation_id: str,
        *,
        modified_by: str,
        reason: str,
    ) -> dict[str, Any]:
        value = str(operation_id or "").strip()
        actor = str(modified_by or "").strip()
        reason_text = str(reason or "").strip()
        if not _ID_RE.fullmatch(value):
            raise TemplateMigrationError("operation_id 必须是完整 SHA-256")
        if not actor:
            raise TemplateMigrationError("modified_by 不能为空")
        if not reason_text:
            raise TemplateMigrationError("rollback reason 不能为空")
        with self.store.transaction_lock():
            if self._pending_journal_exists():
                raise TemplateMigrationIntegrityError(
                    "存在未恢复的模板迁移 journal，拒绝 rollback"
                )
            original = _read_json_file(self.operations_dir / f"{value}.json")
            if original.get("status") != "committed":
                raise TemplateMigrationConflictError("只有 committed 操作可以 rollback")

            manifest_exists, manifest_sha256 = self._artifact_state(
                self.store.manifest_path
            )
            if not self._matches_state(
                exists=manifest_exists,
                digest=manifest_sha256,
                expected_exists=bool(original.get("manifest_after_exists")),
                expected_digest=original.get("manifest_after_sha256"),
            ):
                raise TemplateMigrationConflictError(
                    "manifest 已有后续修改，拒绝 rollback"
                )

            reverse_items: list[dict[str, Any]] = []
            items = original.get("items")
            if not isinstance(items, list) or not items:
                raise TemplateMigrationIntegrityError("原操作缺少 items")
            for item in items:
                runtime_path = self._runtime_path_from_relative(
                    str(item.get("relative_path") or "")
                )
                current_exists, current_sha256 = self._artifact_state(runtime_path)
                if not self._matches_state(
                    exists=current_exists,
                    digest=current_sha256,
                    expected_exists=bool(item.get("after_exists")),
                    expected_digest=item.get("after_sha256"),
                ):
                    raise TemplateMigrationConflictError(
                        f"模板 {item.get('template_key')} 已有后续修改，拒绝 rollback"
                    )
                if item.get("after_exists"):
                    self.store.read_blob_by_sha256(
                        str(item.get("after_blob_sha256") or "")
                    )
                if item.get("before_exists"):
                    self.store.read_blob_by_sha256(
                        str(item.get("before_blob_sha256") or "")
                    )
                reverse_items.append(
                    {
                        "template_key": item["template_key"],
                        "relative_path": item["relative_path"],
                        "before_exists": bool(item["after_exists"]),
                        "before_sha256": item["after_sha256"],
                        "before_blob_sha256": item["after_blob_sha256"],
                        "after_exists": bool(item["before_exists"]),
                        "after_sha256": item["before_sha256"],
                        "after_blob_sha256": item["before_blob_sha256"],
                        "installed": False,
                    }
                )

            if original.get("manifest_after_exists"):
                self.store.read_blob_by_sha256(
                    str(original.get("manifest_after_blob_sha256") or "")
                )
            if original.get("manifest_before_exists"):
                self.store.read_blob_by_sha256(
                    str(original.get("manifest_before_blob_sha256") or "")
                )
            rollback_payload = {
                "rollback_of": value,
                "modified_by": actor,
                "reason": reason_text,
                "manifest_sha256": manifest_sha256,
                "items": [
                    {
                        "template_key": item["template_key"],
                        "before_sha256": item["before_sha256"],
                        "after_sha256": item["after_sha256"],
                    }
                    for item in reverse_items
                ],
            }
            rollback_id = _payload_id(rollback_payload)
            journal = {
                "schema_version": _JOURNAL_SCHEMA_VERSION,
                "operation_id": rollback_id,
                "plan_id": None,
                "operation_type": "rollback",
                "operation_status": "rolled_back",
                "rollback_of": value,
                "modified_by": actor,
                "reason": reason_text,
                "runtime_root": str(self.runtime_dir),
                "state_root": str(self.state_dir),
                "state": "prepared",
                "manifest_before_exists": bool(
                    original.get("manifest_after_exists")
                ),
                "manifest_before_sha256": original.get(
                    "manifest_after_sha256"
                ),
                "manifest_before_blob_sha256": original.get(
                    "manifest_after_blob_sha256"
                ),
                "manifest_after_exists": bool(
                    original.get("manifest_before_exists")
                ),
                "manifest_after_sha256": original.get(
                    "manifest_before_sha256"
                ),
                "manifest_after_blob_sha256": original.get(
                    "manifest_before_blob_sha256"
                ),
                "items": reverse_items,
            }
            self._write_journal(journal)
            self._install_journal_after(journal, inject_failures=True)
            return {
                "status": "rolled_back",
                "operation_id": rollback_id,
                "rollback_of": value,
            }
