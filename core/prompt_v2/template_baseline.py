"""Prompt Runtime 模板基线、原始字节摘要与漂移审计。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterator, Literal

from core.prompt_v2.flow_storage import (
    FlowStorageError,
    atomic_replace_bytes,
    ensure_directory_without_symlinks,
    fsync_directory,
    read_regular_bytes,
    template_governance_write_lock,
)


DriftStatus = Literal[
    "in_sync",
    "upgrade_available",
    "local_override",
    "diverged",
    "runtime_missing",
    "untracked_legacy",
    "invalid",
]
InvalidComponent = Literal[
    "runtime_content",
    "canonical_content",
    "baseline_state",
    "manifest_state",
    "journal_state",
    "storage",
]

DRIFT_STATUSES = frozenset(
    {
        "in_sync",
        "upgrade_available",
        "local_override",
        "diverged",
        "runtime_missing",
        "untracked_legacy",
        "invalid",
    }
)
_MANIFEST_SCHEMA_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset(
    {"schema_version", "revision", "templates", "lineage"}
)
_ENTRY_FIELDS = frozenset(
    {
        "template_key",
        "baseline_version",
        "baseline_sha256",
        "baseline_blob_sha256",
        "canonical_sha256",
        "runtime_sha256",
        "modified_by",
        "modification_source",
        "last_migration_id",
    }
)


class TemplateBaselineError(ValueError):
    """模板基线状态不完整、不安全或无法按请求变更。"""


class TemplateBlobIntegrityError(TemplateBaselineError):
    """模板基线 blob 缺失或内容摘要不匹配。"""


@dataclass(frozen=True)
class TemplateDriftReport:
    template_key: str
    drift_status: DriftStatus
    default_path: str
    runtime_path: str
    default_sha256: str | None
    runtime_sha256: str | None
    baseline_sha256: str | None
    baseline_version: str | None
    invalid_component: InvalidComponent | None = None
    invalid_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def default_template_state_dir(runtime_dir: Path) -> Path:
    configured = str(os.environ.get("NANOBOT_PROMPT_TEMPLATE_STATE_DIR") or "").strip()
    if configured:
        return Path(configured)
    return Path(runtime_dir).parent / "prompt_template_state"


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_disjoint_roots(*roots: Path) -> None:
    absolute = [_absolute(path) for path in roots]
    for index, left in enumerate(absolute):
        for right in absolute[index + 1 :]:
            if left == right or _contains(left, right) or _contains(right, left):
                raise TemplateBaselineError(
                    "模板默认目录、runtime 模板根目录与状态目录必须彼此独立，"
                    "状态目录必须位于 runtime 模板根目录之外"
                )


def _empty_manifest() -> dict[str, Any]:
    return {
        "schema_version": _MANIFEST_SCHEMA_VERSION,
        "revision": 0,
        "templates": {},
        "lineage": [],
    }


def serialize_manifest(manifest: dict[str, Any]) -> bytes:
    _validate_manifest(manifest)
    return (
        json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def parse_manifest_bytes(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateBaselineError(
            "模板基线 manifest 不是有效 UTF-8 JSON"
        ) from exc
    return _validate_manifest(payload)


def _validate_sha256(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not _SHA256_RE.fullmatch(text):
        raise TemplateBaselineError(f"模板基线字段 {field} 不是完整 SHA-256")
    return text


def _read_regular_bytes(path: Path, *, missing_ok: bool = False) -> bytes | None:
    try:
        return read_regular_bytes(path, missing_ok=missing_ok)
    except FlowStorageError as exc:
        raise TemplateBaselineError(str(exc)) from exc


def _validate_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise TemplateBaselineError("模板基线 manifest 顶层必须是对象")
    if raw.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise TemplateBaselineError("模板基线 manifest schema_version 不受支持")
    if set(raw) != _MANIFEST_FIELDS:
        raise TemplateBaselineError("模板基线 manifest 字段集合非法")
    revision = raw.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise TemplateBaselineError("模板基线 manifest revision 非法")
    templates = raw.get("templates")
    lineage = raw.get("lineage")
    if not isinstance(templates, dict):
        raise TemplateBaselineError("模板基线 manifest templates 必须是对象")
    if not isinstance(lineage, list):
        raise TemplateBaselineError("模板基线 manifest lineage 必须是数组")
    if any(not isinstance(item, str) or not _SHA256_RE.fullmatch(item) for item in lineage):
        raise TemplateBaselineError("模板基线 manifest lineage 包含非法 operation ID")
    for raw_key, entry in templates.items():
        key = str(raw_key or "").strip()
        if not key or not isinstance(entry, dict):
            raise TemplateBaselineError("模板基线 manifest entry 非法")
        if set(entry) != _ENTRY_FIELDS:
            raise TemplateBaselineError(f"模板基线记录 {key} 字段集合非法")
        if str(entry.get("template_key") or "").strip() != key:
            raise TemplateBaselineError(f"模板基线记录 {key} 的 template_key 不一致")
        if not str(entry.get("baseline_version") or "").strip():
            raise TemplateBaselineError(f"模板基线记录 {key} 缺少 baseline_version")
        _validate_sha256(entry.get("baseline_sha256"), field=f"{key}.baseline_sha256")
        _validate_sha256(
            entry.get("baseline_blob_sha256"),
            field=f"{key}.baseline_blob_sha256",
        )
        for field in ("canonical_sha256", "runtime_sha256"):
            value = entry.get(field)
            if value is not None:
                _validate_sha256(value, field=f"{key}.{field}")
        if not str(entry.get("modified_by") or "").strip():
            raise TemplateBaselineError(f"模板基线记录 {key} 缺少 modified_by")
        if not str(entry.get("modification_source") or "").strip():
            raise TemplateBaselineError(
                f"模板基线记录 {key} 缺少 modification_source"
            )
        last_migration_id = entry.get("last_migration_id")
        if last_migration_id is not None and not str(last_migration_id).strip():
            raise TemplateBaselineError(
                f"模板基线记录 {key} 的 last_migration_id 非法"
            )
    return raw


def template_version(content: bytes, *, fallback: str) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return fallback
    if text.startswith("---"):
        lines = text.splitlines()
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if line.lstrip().startswith("version:"):
                value = line.split(":", 1)[1].strip().strip("\"'")
                if value:
                    return value
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return fallback
    if isinstance(payload, dict):
        value = str(payload.get("version") or "").strip()
        if value:
            return value
    return fallback


class TemplateBaselineStore:
    """以 manifest + content-addressed blob 管理模板安装基线。"""

    def __init__(
        self,
        *,
        default_dir: Path,
        runtime_dir: Path,
        state_dir: Path,
    ) -> None:
        self.default_dir = _absolute(Path(default_dir))
        self.runtime_dir = _absolute(Path(runtime_dir))
        self.state_dir = _absolute(Path(state_dir))
        _validate_disjoint_roots(
            self.default_dir,
            self.runtime_dir,
            self.state_dir,
        )

    @classmethod
    def from_environment(cls) -> TemplateBaselineStore:
        from core.prompt_v2.template_registry import (
            default_template_dir,
            runtime_template_dir,
        )

        runtime_dir = runtime_template_dir()
        return cls(
            default_dir=default_template_dir(),
            runtime_dir=runtime_dir,
            state_dir=default_template_state_dir(runtime_dir),
        )

    @property
    def manifest_path(self) -> Path:
        return self.state_dir / "manifest.json"

    @property
    def blob_dir(self) -> Path:
        return self.state_dir / "blobs"

    @contextmanager
    def transaction_lock(self) -> Iterator[None]:
        with template_governance_write_lock(self.runtime_dir):
            ensure_directory_without_symlinks(self.state_dir)
            yield

    def _relative_path(self, template_key: str) -> Path:
        from core.prompt_v2.template_registry import resolve_template_key

        key = resolve_template_key(template_key)
        suffix = ".json" if key == "chat/flow" else ".md"
        return Path(f"{key}{suffix}")

    def _paths(self, template_key: str) -> tuple[str, Path, Path]:
        from core.prompt_v2.template_registry import resolve_template_key

        key = resolve_template_key(template_key)
        relative = self._relative_path(key)
        return key, self.default_dir / relative, self.runtime_dir / relative

    def _load_manifest(self) -> dict[str, Any]:
        raw = _read_regular_bytes(self.manifest_path, missing_ok=True)
        if raw is None:
            return _empty_manifest()
        return parse_manifest_bytes(raw)

    def manifest_snapshot(self) -> dict[str, Any]:
        return json.loads(
            json.dumps(self._load_manifest(), ensure_ascii=False)
        )

    def manifest_bytes(self) -> bytes | None:
        return _read_regular_bytes(self.manifest_path, missing_ok=True)

    def write_manifest_snapshot(self, manifest: dict[str, Any]) -> None:
        self._write_manifest(manifest)

    def write_manifest_bytes(self, raw: bytes) -> None:
        parse_manifest_bytes(raw)
        atomic_replace_bytes(self.manifest_path, raw)

    def has_pending_journal(self) -> bool:
        return (
            _read_regular_bytes(
                self.state_dir / "journals" / "pending.json",
                missing_ok=True,
            )
            is not None
        )

    def record_runtime_observation_locked(
        self,
        template_key: str,
        *,
        modification_source: str,
    ) -> bool:
        """在调用方持有治理写锁时更新已接管模板的当前摘要。"""
        source = str(modification_source or "").strip()
        if not source:
            raise TemplateBaselineError("modification_source 不能为空")
        key, default_path, runtime_path = self._paths(template_key)
        manifest = self._load_manifest()
        entry = manifest["templates"].get(key)
        if entry is None:
            return False
        if not isinstance(entry, dict):
            raise TemplateBaselineError(f"模板基线记录 {key} 必须是对象")
        self.read_baseline_bytes(key)
        default_bytes = _read_regular_bytes(default_path, missing_ok=True)
        runtime_bytes = _read_regular_bytes(runtime_path, missing_ok=True)
        entry["canonical_sha256"] = (
            sha256_bytes(default_bytes) if default_bytes is not None else None
        )
        entry["runtime_sha256"] = (
            sha256_bytes(runtime_bytes) if runtime_bytes is not None else None
        )
        entry["modified_by"] = source
        entry["modification_source"] = source
        manifest["revision"] += 1
        self._write_manifest(manifest)
        return True

    def template_paths(self, template_key: str) -> tuple[str, Path, Path]:
        return self._paths(template_key)

    def install_blob_once(self, content: bytes) -> str:
        return self._write_blob_once(content)

    def read_blob_by_sha256(self, digest: str) -> bytes:
        value = _validate_sha256(digest, field="blob_sha256")
        try:
            content = _read_regular_bytes(self.blob_dir / value)
        except (FileNotFoundError, TemplateBaselineError) as exc:
            raise TemplateBlobIntegrityError(
                f"模板基线 blob 缺失或不可读: {value}"
            ) from exc
        if content is None or sha256_bytes(content) != value:
            raise TemplateBlobIntegrityError(
                f"模板基线 blob 内容摘要不匹配: {value}"
            )
        return content

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        atomic_replace_bytes(self.manifest_path, serialize_manifest(manifest))

    def _write_blob_once(self, content: bytes) -> str:
        digest = sha256_bytes(content)
        blob_dir = ensure_directory_without_symlinks(self.blob_dir)
        blob_path = blob_dir / digest
        existing = _read_regular_bytes(blob_path, missing_ok=True)
        if existing is not None:
            if sha256_bytes(existing) != digest:
                raise TemplateBlobIntegrityError(
                    f"模板基线 blob 内容摘要不匹配: {digest}"
                )
            return digest

        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{digest}.",
            suffix=".tmp",
            dir=blob_dir,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temp_path, blob_path, follow_symlinks=False)
            except FileExistsError:
                existing = _read_regular_bytes(blob_path)
                if existing is None or sha256_bytes(existing) != digest:
                    raise TemplateBlobIntegrityError(
                        f"模板基线 blob 内容摘要不匹配: {digest}"
                    )
            fsync_directory(blob_dir)
        finally:
            temp_path.unlink(missing_ok=True)
        return digest

    def _entry(self, template_key: str) -> dict[str, Any] | None:
        manifest = self._load_manifest()
        entry = manifest["templates"].get(template_key)
        if entry is None:
            return None
        if not isinstance(entry, dict):
            raise TemplateBaselineError(
                f"模板基线记录 {template_key} 必须是对象"
            )
        return entry

    def read_baseline_bytes(self, template_key: str) -> bytes:
        key, _default_path, _runtime_path = self._paths(template_key)
        entry = self._entry(key)
        if entry is None:
            raise TemplateBlobIntegrityError(f"模板 {key} 尚未建立基线")
        baseline_sha256 = _validate_sha256(
            entry.get("baseline_sha256"),
            field=f"{key}.baseline_sha256",
        )
        blob_sha256 = _validate_sha256(
            entry.get("baseline_blob_sha256"),
            field=f"{key}.baseline_blob_sha256",
        )
        if baseline_sha256 != blob_sha256:
            raise TemplateBlobIntegrityError(
                f"模板 {key} 的 manifest 基线摘要与 blob 指针不一致"
            )
        blob_path = self.blob_dir / blob_sha256
        try:
            content = _read_regular_bytes(blob_path)
        except (FileNotFoundError, TemplateBaselineError) as exc:
            raise TemplateBlobIntegrityError(
                f"模板 {key} 的基线 blob 缺失或不可读"
            ) from exc
        if content is None or sha256_bytes(content) != blob_sha256:
            raise TemplateBlobIntegrityError(
                f"模板 {key} 的基线 blob 内容摘要不匹配"
            )
        return content

    def audit(self, template_key: str) -> TemplateDriftReport:
        key, default_path, runtime_path = self._paths(template_key)
        default_bytes: bytes | None = None
        runtime_bytes: bytes | None = None
        default_sha256: str | None = None
        runtime_sha256: str | None = None
        baseline_sha256: str | None = None
        baseline_version: str | None = None

        def invalid_report(
            component: InvalidComponent,
            reason: BaseException | str,
        ) -> TemplateDriftReport:
            return TemplateDriftReport(
                template_key=key,
                drift_status="invalid",
                default_path=str(default_path),
                runtime_path=str(runtime_path),
                default_sha256=default_sha256,
                runtime_sha256=runtime_sha256,
                baseline_sha256=baseline_sha256,
                baseline_version=baseline_version,
                invalid_component=component,
                invalid_reason=str(reason),
            )

        try:
            if self.has_pending_journal():
                raise TemplateBaselineError(
                    "存在 pending journal，必须先完成模板迁移恢复"
                )
        except (TemplateBaselineError, FileNotFoundError) as exc:
            return invalid_report("journal_state", exc)

        try:
            default_bytes = _read_regular_bytes(default_path, missing_ok=True)
            runtime_bytes = _read_regular_bytes(runtime_path, missing_ok=True)
            default_sha256 = (
                sha256_bytes(default_bytes) if default_bytes is not None else None
            )
            runtime_sha256 = (
                sha256_bytes(runtime_bytes) if runtime_bytes is not None else None
            )
        except (TemplateBaselineError, FileNotFoundError) as exc:
            return invalid_report("storage", exc)

        try:
            entry = self._entry(key)
        except (TemplateBaselineError, FileNotFoundError) as exc:
            return invalid_report("manifest_state", exc)

        from core.prompt_v2.template_validation import (
            TemplateContentValidationError,
            validate_template_bytes,
        )

        try:
            if default_bytes is not None:
                validate_template_bytes(key, default_bytes)
        except TemplateContentValidationError as exc:
            return invalid_report("canonical_content", exc)

        if entry is not None:
            try:
                baseline_sha256 = _validate_sha256(
                    entry.get("baseline_sha256"),
                    field=f"{key}.baseline_sha256",
                )
                baseline_version = str(
                    entry.get("baseline_version") or ""
                ).strip()
                if not baseline_version:
                    raise TemplateBaselineError(
                        f"模板基线记录 {key} 缺少 baseline_version"
                    )
                baseline_bytes = self.read_baseline_bytes(key)
                validate_template_bytes(
                    key,
                    baseline_bytes,
                    require_runtime_contract=False,
                )
            except (
                TemplateBaselineError,
                FileNotFoundError,
                TemplateContentValidationError,
            ) as exc:
                return invalid_report("baseline_state", exc)

            if default_bytes is None:
                return invalid_report(
                    "canonical_content",
                    f"模板 {key} 的 canonical 文件缺失",
                )

        try:
            if runtime_bytes is not None:
                validate_template_bytes(
                    key,
                    runtime_bytes,
                    require_runtime_contract=entry is not None,
                )
        except TemplateContentValidationError as exc:
            return invalid_report("runtime_content", exc)

        if entry is None:
            status: DriftStatus = (
                "runtime_missing"
                if runtime_bytes is None
                else "untracked_legacy"
            )
            return TemplateDriftReport(
                template_key=key,
                drift_status=status,
                default_path=str(default_path),
                runtime_path=str(runtime_path),
                default_sha256=default_sha256,
                runtime_sha256=runtime_sha256,
                baseline_sha256=None,
                baseline_version=None,
            )

        try:
            baseline_sha256 = _validate_sha256(
                entry.get("baseline_sha256"),
                field=f"{key}.baseline_sha256",
            )
            if runtime_bytes is None:
                status = "runtime_missing"
            elif runtime_sha256 == default_sha256:
                status = "in_sync"
            elif runtime_sha256 == baseline_sha256:
                status = "upgrade_available"
            elif default_sha256 == baseline_sha256:
                status = "local_override"
            else:
                status = "diverged"
            return TemplateDriftReport(
                template_key=key,
                drift_status=status,
                default_path=str(default_path),
                runtime_path=str(runtime_path),
                default_sha256=default_sha256,
                runtime_sha256=runtime_sha256,
                baseline_sha256=baseline_sha256,
                baseline_version=baseline_version,
            )
        except (TemplateBaselineError, FileNotFoundError) as exc:
            return invalid_report("baseline_state", exc)

    def _record_baseline(
        self,
        *,
        key: str,
        content: bytes,
        baseline_version: str,
        modified_by: str,
        runtime_sha256: str,
        migration_id: str | None,
    ) -> None:
        version = str(baseline_version or "").strip()
        actor = str(modified_by or "").strip()
        if not version:
            raise TemplateBaselineError("baseline_version 不能为空")
        if not actor:
            raise TemplateBaselineError("modified_by 不能为空")
        digest = self._write_blob_once(content)
        manifest = self._load_manifest()
        if key in manifest["templates"]:
            raise TemplateBaselineError(f"模板 {key} 已存在基线记录")
        manifest["templates"][key] = {
            "template_key": key,
            "baseline_version": version,
            "baseline_sha256": digest,
            "baseline_blob_sha256": digest,
            "canonical_sha256": digest,
            "runtime_sha256": runtime_sha256,
            "modified_by": actor,
            "modification_source": migration_id or actor,
            "last_migration_id": migration_id,
        }
        manifest["revision"] += 1
        self._write_manifest(manifest)

    def adopt_in_sync(
        self,
        template_key: str,
        *,
        baseline_version: str,
        modified_by: str,
    ) -> TemplateDriftReport:
        key, default_path, runtime_path = self._paths(template_key)
        with self.transaction_lock():
            if self._entry(key) is not None:
                raise TemplateBaselineError(f"模板 {key} 已存在基线记录")
            default_bytes = _read_regular_bytes(default_path)
            runtime_bytes = _read_regular_bytes(runtime_path)
            if default_bytes is None or runtime_bytes is None:
                raise TemplateBaselineError(f"模板 {key} 缺少 canonical 或 runtime 文件")
            if runtime_bytes != default_bytes:
                raise TemplateBaselineError(
                    f"模板 {key} 的 runtime 与 canonical 内容不一致，不能 adopt-in-sync"
                )
            from core.prompt_v2.template_validation import (
                TemplateContentValidationError,
                validate_template_bytes,
            )

            try:
                validate_template_bytes(key, default_bytes)
            except TemplateContentValidationError as exc:
                raise TemplateBaselineError(str(exc)) from exc
            self._record_baseline(
                key=key,
                content=default_bytes,
                baseline_version=baseline_version,
                modified_by=modified_by,
                runtime_sha256=sha256_bytes(runtime_bytes),
                migration_id="adopt-in-sync",
            )
        return self.audit(key)

    def provision_missing(
        self,
        template_key: str,
        *,
        modified_by: str = "startup-provision",
    ) -> bool:
        from core.prompt_v2.template_migration import TemplateMigrationService

        return TemplateMigrationService(
            default_dir=self.default_dir,
            runtime_dir=self.runtime_dir,
            state_dir=self.state_dir,
        ).provision_missing(
            template_key,
            modified_by=modified_by,
        )

    def list_template_keys(self) -> list[str]:
        from core.prompt_v2.template_registry import resolve_template_key

        keys: set[str] = set()
        for root in (self.default_dir, self.runtime_dir):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.suffix not in {".md", ".json"}:
                    continue
                relative = path.relative_to(root).with_suffix("").as_posix()
                try:
                    keys.add(resolve_template_key(relative))
                except ValueError:
                    continue
        manifest = self._load_manifest()
        keys.update(str(key) for key in manifest["templates"])
        return sorted(keys)

    def audit_all(self) -> list[TemplateDriftReport]:
        return [self.audit(key) for key in self.list_template_keys()]
