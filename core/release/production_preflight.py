"""正式 Runtime 发布前的宿主路径、备份与 Prompt 证据门禁。"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
from typing import Any, Mapping

from core.release.artifacts import ArtifactManifest
from core.registry.validation import canonical_json


SYSTEM_MIN_FREE_BYTES = 60 * 1024 * 1024 * 1024
DEFAULT_PULL_RESERVE_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_EVIDENCE_MAX_AGE_SECONDS = 6 * 60 * 60

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BACKUP_HASH_LINE_RE = re.compile(
    r"^(?P<sha256>[0-9a-f]{64})  (?P<name>[A-Za-z0-9_.-]+)$"
)
_BACKUP_FILES = (
    "nanobot.db",
    "workspaces.tar",
    "assets.tar",
    "manifest.txt",
)
_PROMPT_RECEIPT_FIELDS = frozenset({
    "schema_version",
    "created_at",
    "image_reference",
    "git_full_commit",
    "prompt_defaults_sha256",
    "host_prompt_root_sha256",
    "accepted_local_overrides",
    "findings",
    "passed",
    "sha256",
})
_FEATURE_SETTING_KEYS = (
    "sandbox.enabled",
    "sandbox.exec_enabled",
    "sandbox.group_enabled",
    "group_learning.enabled",
    "group_memory.injection_enabled",
)


class ProductionPreflightError(RuntimeError):
    """生产前提缺失或证据不满足 fail-closed 合同。"""


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ("git", "-C", str(root), *arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProductionPreflightError("无法读取独立发布树 Git 身份") from exc
    if result.returncode != 0:
        raise ProductionPreflightError("无法读取独立发布树 Git 身份")
    return result.stdout.strip()


def validate_release_source_identity(
    source_root: Path,
    artifact: ArtifactManifest,
) -> None:
    """保证执行部署的 Compose 与目标 Artifact 来自同一干净源码快照。"""

    source = _require_plain_path(
        source_root,
        field_name="发布源码根目录",
        expect_directory=True,
    )
    if _git(source, "rev-parse", "HEAD") != artifact.source.git_full_commit:
        raise ProductionPreflightError("独立发布树 HEAD 与 ReleaseManifest 不一致")
    if _git(source, "status", "--porcelain", "--untracked-files=no"):
        raise ProductionPreflightError("独立发布树存在 tracked 修改")
    kt_root = source / "vendor" / "KohakuTerrarium"
    if _git(kt_root, "rev-parse", "HEAD") != artifact.source.kt_commit:
        raise ProductionPreflightError("独立发布树 KT commit 与 ReleaseManifest 不一致")


def _safe_artifact_path(
    source_root: Path,
    relative_path: str,
    *,
    field_name: str,
) -> Path:
    candidate = source_root / relative_path
    resolved = _require_plain_path(
        candidate,
        field_name=field_name,
        expect_directory=False,
    )
    if not _contains(source_root, resolved):
        raise ProductionPreflightError(f"{field_name} 逃逸独立发布树")
    return resolved


def _hash_repository_path(path: Path) -> str:
    if path.is_symlink():
        raise ProductionPreflightError("发布输入不能是符号链接")
    if path.is_file():
        return _sha256_file(path)
    if not path.is_dir():
        raise ProductionPreflightError("发布输入不是普通文件或目录")
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_symlink():
            raise ProductionPreflightError("发布输入包含符号链接")
        if not child.is_file():
            continue
        relative = child.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(child).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def validate_release_artifact_evidence(
    source_root: Path,
    artifact: ArtifactManifest,
) -> None:
    """重算 SBOM、锁文件、Prompt/Web 输入和验证结果的绑定 Hash。"""

    source = _require_plain_path(
        source_root,
        field_name="发布源码根目录",
        expect_directory=True,
    )
    sbom = _safe_artifact_path(
        source,
        artifact.sbom_path,
        field_name="Runtime SBOM",
    )
    dependency = _safe_artifact_path(
        source,
        artifact.dependency_manifest_path,
        field_name="生产依赖锁",
    )
    verification = _safe_artifact_path(
        source,
        artifact.verification_results_path,
        field_name="服务器验证结果",
    )
    if _sha256_file(sbom) != artifact.sbom_sha256:
        raise ProductionPreflightError("Runtime SBOM Hash 与 ArtifactManifest 不一致")
    dependency_sha256 = _sha256_file(dependency)
    if dependency_sha256 != artifact.dependency_manifest_sha256:
        raise ProductionPreflightError("生产依赖锁 Hash 与 ArtifactManifest 不一致")
    if dependency_sha256 != artifact.input_hashes.get("python_lock"):
        raise ProductionPreflightError("生产依赖锁与构建输入 Hash 不一致")
    if _sha256_file(verification) != artifact.verification_results_sha256:
        raise ProductionPreflightError("验证结果 Hash 与 ArtifactManifest 不一致")

    expected_inputs = {
        "web_lock": source / "webui" / "package-lock.json",
        "prompt_defaults": source / "prompts.v2.default",
    }
    for input_name, path in expected_inputs.items():
        expected = artifact.input_hashes.get(input_name)
        if not expected or _hash_repository_path(path) != expected:
            raise ProductionPreflightError(
                f"发布输入 {input_name} Hash 与 ArtifactManifest 不一致"
            )

    try:
        sbom_payload = json.loads(sbom.read_text(encoding="utf-8"))
        verification_payload = json.loads(
            verification.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionPreflightError("SBOM 或验证结果不是有效 JSON") from exc
    if (
        not isinstance(sbom_payload, dict)
        or not str(sbom_payload.get("spdxVersion") or "").startswith("SPDX-")
    ):
        raise ProductionPreflightError("Runtime SBOM 不是有效 SPDX JSON")
    if not isinstance(verification_payload, dict):
        raise ProductionPreflightError("验证结果格式无效")
    if (
        verification_payload.get("schema_version") != 1
        or verification_payload.get("source_sha")
        != artifact.source.git_full_commit
        or verification_payload.get("kt_sha") != artifact.source.kt_commit
    ):
        raise ProductionPreflightError("验证结果源码身份与 ArtifactManifest 不一致")
    suites = verification_payload.get("suites")
    if not isinstance(suites, dict) or set(suites) != set(
        artifact.verification_suites
    ):
        raise ProductionPreflightError("验证结果 suite 集合与 ArtifactManifest 不一致")
    for suite_name, result in suites.items():
        if (
            not isinstance(result, dict)
            or result.get("conclusion") != "success"
            or not str(result.get("run_id") or "").strip()
            or not str(result.get("job") or "").strip()
        ):
            raise ProductionPreflightError(
                f"验证 suite {suite_name} 没有可信 success 证据"
            )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_plain_path(
    value: Path,
    *,
    field_name: str,
    expect_directory: bool,
) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ProductionPreflightError(f"{field_name} 必须是绝对路径")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ProductionPreflightError(
                f"{field_name} 不能包含符号链接"
            )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProductionPreflightError(
            f"{field_name} 不存在或不可访问"
        ) from exc
    valid_type = resolved.is_dir() if expect_directory else resolved.is_file()
    if not valid_type:
        expected = "目录" if expect_directory else "普通文件"
        raise ProductionPreflightError(f"{field_name} 必须是{expected}")
    return resolved


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_production_paths(
    *,
    source_root: Path,
    production_root: Path,
    environment_file: Path,
    data_dir: Path,
    models_dir: Path,
    sentinel_dir: Path,
    prompt_host_root: Path,
    release_state_dir: Path,
) -> dict[str, Path]:
    """拒绝把可变生产状态重新放回任一 Git 发布树。"""

    source = _require_plain_path(
        source_root,
        field_name="发布源码根目录",
        expect_directory=True,
    )
    production = _require_plain_path(
        production_root,
        field_name="生产数据根目录",
        expect_directory=True,
    )
    if source == production:
        raise ProductionPreflightError(
            "正式部署必须从独立发布树执行，不能使用生产 checkout"
        )
    environment = _require_plain_path(
        environment_file,
        field_name="生产环境文件",
        expect_directory=False,
    )
    data = _require_plain_path(
        data_dir,
        field_name="生产 data 目录",
        expect_directory=True,
    )
    models = _require_plain_path(
        models_dir,
        field_name="生产 models 目录",
        expect_directory=True,
    )
    sentinel = _require_plain_path(
        sentinel_dir,
        field_name="生产 sentinel 目录",
        expect_directory=True,
    )
    prompt = _require_plain_path(
        prompt_host_root,
        field_name="Prompt Runtime 宿主根目录",
        expect_directory=True,
    )
    state = _require_plain_path(
        release_state_dir,
        field_name="Release 状态目录",
        expect_directory=True,
    )

    expected_children = {
        "生产环境文件": (environment, production / ".env"),
        "生产 data 目录": (data, production / "data"),
        "生产 models 目录": (models, production / "models"),
        "生产 sentinel 目录": (sentinel, production / "sentinel"),
    }
    for field_name, (actual, expected) in expected_children.items():
        if actual != expected:
            raise ProductionPreflightError(
                f"{field_name} 与 NANOBOT_PRODUCTION_ROOT 不一致"
            )

    for mutable_name, mutable_path in (
        ("Prompt Runtime", prompt),
        ("Release 状态", state),
    ):
        if _contains(source, mutable_path) or _contains(production, mutable_path):
            raise ProductionPreflightError(
                f"{mutable_name} 必须位于源码与生产 checkout 之外"
            )
    if _contains(prompt, state) or _contains(state, prompt):
        raise ProductionPreflightError(
            "Prompt Runtime 与 Release 状态目录必须彼此独立"
        )
    return {
        "source_root": source,
        "production_root": production,
        "environment_file": environment,
        "data_dir": data,
        "models_dir": models,
        "sentinel_dir": sentinel,
        "prompt_host_root": prompt,
        "release_state_dir": state,
    }


def _parse_manifest(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProductionPreflightError("无法读取协调备份 manifest") from exc
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise ProductionPreflightError("协调备份 manifest 格式无效")
        values[key] = value
    return values


def _parse_utc_timestamp(value: str, *, field_name: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise ProductionPreflightError(
            f"{field_name} 时间格式无效"
        ) from exc
    return parsed


def _assert_fresh(
    created_at: datetime,
    *,
    now: datetime,
    max_age_seconds: int,
    field_name: str,
) -> None:
    if max_age_seconds <= 0:
        raise ProductionPreflightError("证据最大有效期必须大于 0")
    age = (now - created_at).total_seconds()
    if age < -300:
        raise ProductionPreflightError(f"{field_name} 时间晚于当前主机")
    if age > max_age_seconds:
        raise ProductionPreflightError(f"{field_name} 已超过允许有效期")


def validate_coordinated_backup(
    *,
    backup_dir: Path,
    database_path: Path,
    data_root: Path,
    expected_risk_marker: str,
    max_age_seconds: int = DEFAULT_EVIDENCE_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, str]:
    """验证协调备份文件、Hash、SQLite 和风险标记，不读取业务行。"""

    backup = _require_plain_path(
        backup_dir,
        field_name="协调备份目录",
        expect_directory=True,
    )
    database = _require_plain_path(
        database_path,
        field_name="生产 SQLite",
        expect_directory=False,
    )
    data = _require_plain_path(
        data_root,
        field_name="Sandbox 数据根目录",
        expect_directory=True,
    )
    files = {
        name: _require_plain_path(
            backup / name,
            field_name=f"协调备份 {name}",
            expect_directory=False,
        )
        for name in (*_BACKUP_FILES, "manifest.sha256")
    }

    expected_hashes: dict[str, str] = {}
    try:
        hash_lines = files["manifest.sha256"].read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProductionPreflightError("无法读取协调备份 Hash 清单") from exc
    for line in hash_lines:
        match = _BACKUP_HASH_LINE_RE.fullmatch(line)
        if match is None:
            raise ProductionPreflightError("协调备份 Hash 清单格式无效")
        name = match.group("name")
        if name in expected_hashes:
            raise ProductionPreflightError("协调备份 Hash 清单包含重复文件")
        expected_hashes[name] = match.group("sha256")
    if set(expected_hashes) != set(_BACKUP_FILES):
        raise ProductionPreflightError("协调备份 Hash 清单文件集合不完整")
    for name in _BACKUP_FILES:
        if _sha256_file(files[name]) != expected_hashes[name]:
            raise ProductionPreflightError(
                f"协调备份 {name} 的 SHA-256 校验失败"
            )

    manifest = _parse_manifest(files["manifest.txt"])
    required_manifest = {
        "created_at",
        "data_root",
        "database",
        "backup_mode",
        "backup_risk_marker",
        "quiesced",
        "runtime_included",
        "input_staging_included",
    }
    if not required_manifest.issubset(manifest):
        raise ProductionPreflightError("协调备份 manifest 缺少必需字段")
    if Path(manifest["database"]).resolve() != database:
        raise ProductionPreflightError("协调备份不属于当前生产 SQLite")
    if Path(manifest["data_root"]).resolve() != data:
        raise ProductionPreflightError("协调备份不属于当前 Sandbox 数据根目录")
    if manifest["backup_risk_marker"] != expected_risk_marker:
        raise ProductionPreflightError("协调备份风险标记与部署策略不一致")
    expected_backup_mode = (
        "local_same_disk"
        if expected_risk_marker == "single_disk_logical_rollback_only"
        else "independent"
    )
    if manifest["backup_mode"] != expected_backup_mode:
        raise ProductionPreflightError("协调备份模式与风险标记不一致")
    if manifest["quiesced"] != "true":
        raise ProductionPreflightError("协调备份没有 quiesced 证据")
    if (
        manifest["runtime_included"] != "false"
        or manifest["input_staging_included"] != "false"
    ):
        raise ProductionPreflightError("协调备份包含了禁止归档的临时 Runtime 数据")

    current_time = now or datetime.now(timezone.utc)
    _assert_fresh(
        _parse_utc_timestamp(
            manifest["created_at"],
            field_name="协调备份 created_at",
        ),
        now=current_time,
        max_age_seconds=max_age_seconds,
        field_name="协调备份",
    )
    try:
        connection = sqlite3.connect(
            f"file:{files['nanobot.db']}?mode=ro",
            uri=True,
        )
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ProductionPreflightError("无法校验协调备份 SQLite") from exc
    if quick_check != ("ok",):
        raise ProductionPreflightError("协调备份 SQLite quick_check 未通过")
    return {
        "backup_dir": str(backup),
        "created_at": manifest["created_at"],
        "manifest_sha256": _sha256_file(files["manifest.txt"]),
        "risk_marker": manifest["backup_risk_marker"],
    }


def _parse_iso_datetime(value: object, *, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ProductionPreflightError(f"{field_name} 时间格式无效") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProductionPreflightError(f"{field_name} 必须包含时区")
    return parsed.astimezone(timezone.utc)


def validate_prompt_audit_receipt(
    *,
    receipt_path: Path,
    prompt_host_root: Path,
    artifact: ArtifactManifest,
    max_age_seconds: int = DEFAULT_EVIDENCE_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    """验证由目标 digest 生成且只包含 key／状态／Hash 的 Prompt 回执。"""

    prompt = _require_plain_path(
        prompt_host_root,
        field_name="Prompt Runtime 宿主根目录",
        expect_directory=True,
    )
    receipt = _require_plain_path(
        receipt_path,
        field_name="Prompt Runtime 审计回执",
        expect_directory=False,
    )
    receipts_root = _require_plain_path(
        prompt / "receipts",
        field_name="Prompt Runtime 回执目录",
        expect_directory=True,
    )
    if not _contains(receipts_root, receipt):
        raise ProductionPreflightError(
            "Prompt Runtime 审计回执必须位于受管回执目录"
        )
    receipt_stat = receipt.stat()
    if (
        receipt_stat.st_uid != os.geteuid()
        or stat.S_IMODE(receipt_stat.st_mode) != 0o440
    ):
        raise ProductionPreflightError(
            "Prompt Runtime 审计回执必须由 root 持有且权限为 0440"
        )
    try:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionPreflightError("Prompt Runtime 审计回执无法读取") from exc
    if not isinstance(payload, dict) or set(payload) != _PROMPT_RECEIPT_FIELDS:
        raise ProductionPreflightError("Prompt Runtime 审计回执字段无效")
    expected_sha = str(payload.pop("sha256", ""))
    if not _SHA256_RE.fullmatch(expected_sha):
        raise ProductionPreflightError("Prompt Runtime 审计回执 SHA-256 无效")
    if _sha256_text(canonical_json(payload)) != expected_sha:
        raise ProductionPreflightError("Prompt Runtime 审计回执 Hash 校验失败")
    if payload.get("schema_version") != 1 or payload.get("passed") is not True:
        raise ProductionPreflightError("Prompt Runtime 审计尚未通过")
    if payload.get("image_reference") != artifact.oci_image_reference:
        raise ProductionPreflightError("Prompt Runtime 审计镜像与 Release 不一致")
    if payload.get("git_full_commit") != artifact.source.git_full_commit:
        raise ProductionPreflightError("Prompt Runtime 审计 Git SHA 与 Release 不一致")
    if payload.get("prompt_defaults_sha256") != artifact.input_hashes.get(
        "prompt_defaults"
    ):
        raise ProductionPreflightError("Prompt Runtime canonical Hash 与 Release 不一致")
    if payload.get("host_prompt_root_sha256") != _sha256_text(str(prompt)):
        raise ProductionPreflightError("Prompt Runtime 审计回执属于其他宿主路径")
    findings = payload.get("findings")
    accepted = payload.get("accepted_local_overrides")
    if not isinstance(findings, list) or not isinstance(accepted, list):
        raise ProductionPreflightError("Prompt Runtime 审计摘要类型无效")
    _assert_fresh(
        _parse_iso_datetime(
            payload.get("created_at"),
            field_name="Prompt Runtime 审计 created_at",
        ),
        now=now or datetime.now(timezone.utc),
        max_age_seconds=max_age_seconds,
        field_name="Prompt Runtime 审计回执",
    )
    return {**payload, "sha256": expected_sha}


def _coerce_bool(value: str, *, key: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ProductionPreflightError(f"生产设置 {key} 不是合法布尔值")


def validate_database_feature_kill_switches(database_path: Path) -> None:
    """只读确认数据库覆盖没有打开 Sandbox 或群学习能力。"""

    database = _require_plain_path(
        database_path,
        field_name="生产 SQLite",
        expect_directory=False,
    )
    placeholders = ",".join("?" for _ in _FEATURE_SETTING_KEYS)
    try:
        connection = sqlite3.connect(
            f"file:{database}?mode=ro",
            uri=True,
        )
        try:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='system_settings'"
            ).fetchone()
            if table is None:
                raise ProductionPreflightError(
                    "生产数据库缺少 system_settings，无法确认 Feature 状态"
                )
            rows = connection.execute(
                f"SELECT key, value FROM system_settings "
                f"WHERE key IN ({placeholders})",
                _FEATURE_SETTING_KEYS,
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ProductionPreflightError("无法读取生产 Feature 状态") from exc
    enabled = sorted(
        str(key)
        for key, value in rows
        if _coerce_bool(str(value), key=str(key))
    )
    if enabled:
        raise ProductionPreflightError(
            "生产 Feature kill switch 尚未全部关闭：" + ", ".join(enabled)
        )


def validate_pull_disk_gate(
    *,
    path: Path = Path("/"),
    system_min_free_bytes: int = SYSTEM_MIN_FREE_BYTES,
    pull_reserve_bytes: int = DEFAULT_PULL_RESERVE_BYTES,
) -> int:
    """拉取前保留系统水位与显式镜像拉取／解包预算。"""

    if system_min_free_bytes < SYSTEM_MIN_FREE_BYTES:
        raise ProductionPreflightError("系统最低水位不能低于 60 GiB")
    if pull_reserve_bytes <= 0:
        raise ProductionPreflightError("镜像拉取／解包预算必须大于 0")
    try:
        free_bytes = shutil.disk_usage(path).free
    except OSError as exc:
        raise ProductionPreflightError("无法读取部署磁盘水位") from exc
    if free_bytes < system_min_free_bytes + pull_reserve_bytes:
        raise ProductionPreflightError(
            "部署磁盘不足以同时保留 60 GiB 系统水位和镜像拉取／解包预算"
        )
    return free_bytes
