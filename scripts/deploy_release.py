#!/usr/bin/env python3
"""使用 ReleaseManifest 原子切换四个固定 Runtime 服务。"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class SubprocessCommandRunner:
    """不使用 shell，不把命令输出拼进部署异常。"""

    def __init__(self, *, root: Path, timeout_seconds: int) -> None:
        self.root = root
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        args: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
    ):
        from core.release.deployment import CommandResult

        env = os.environ.copy()
        if environment is not None:
            env.update(environment)
        try:
            completed = subprocess.run(
                tuple(args),
                cwd=self.root,
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError):
            return CommandResult(125, "", "")
        return CommandResult(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按 ReleaseManifest 原子部署 Nanobot Runtime"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--state-dir",
        type=Path,
        required=True,
    )
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--compose-env-file", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--sandbox-data-root", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--backup-risk-marker", required=True)
    parser.add_argument("--prompt-host-root", type=Path, required=True)
    parser.add_argument("--prompt-audit-receipt", type=Path)
    parser.add_argument(
        "--evidence-max-age-seconds",
        type=int,
        default=21600,
    )
    parser.add_argument(
        "--system-min-free-bytes",
        type=int,
        default=60 * 1024 * 1024 * 1024,
    )
    parser.add_argument(
        "--pull-reserve-bytes",
        type=int,
        default=5 * 1024 * 1024 * 1024,
    )
    parser.add_argument(
        "--ready-url",
        default="http://127.0.0.1:8000/api/v1/ready",
    )
    parser.add_argument(
        "--health-timeout-seconds",
        type=int,
        default=120,
    )
    parser.add_argument(
        "--health-interval-seconds",
        type=float,
        default=2.0,
    )
    parser.add_argument(
        "--command-timeout-seconds",
        type=int,
        default=600,
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="只输出部署、备份和 Prompt 审计需求，不执行切换",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from core.release.artifacts import (
        ReleaseManifestError,
        load_release_manifest,
    )
    from core.release.deployment import (
        AtomicDeploymentError,
        AtomicRuntimeDeployer,
        DeploymentError,
        ReleaseStateStore,
    )
    from core.release.production_preflight import (
        ProductionPreflightError,
        validate_coordinated_backup,
        validate_database_feature_kill_switches,
        validate_production_paths,
        validate_prompt_audit_receipt,
        validate_pull_disk_gate,
        validate_release_artifact_evidence,
        validate_release_source_identity,
    )

    args = _parser().parse_args(argv)
    runtime_image = os.environ.get(
        "NANOBOT_RUNTIME_IMAGE",
        "",
    ).strip()
    try:
        target = load_release_manifest(args.manifest)
    except ReleaseManifestError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    runtime = target.runtime_artifact
    if runtime.provenance != "built":
        print(
            "目标 ReleaseManifest 必须使用 provenance=built",
            file=sys.stderr,
        )
        return 2
    if runtime_image != runtime.oci_image_reference:
        print(
            "NANOBOT_RUNTIME_IMAGE 与 ReleaseManifest 不一致",
            file=sys.stderr,
        )
        return 2
    if (
        args.health_timeout_seconds <= 0
        or args.health_interval_seconds <= 0
        or args.command_timeout_seconds <= 0
        or args.evidence_max_age_seconds <= 0
        or args.system_min_free_bytes <= 0
        or args.pull_reserve_bytes <= 0
    ):
        print("部署超时、证据有效期与磁盘预算参数必须大于 0", file=sys.stderr)
        return 2

    state_dir = args.state_dir
    try:
        paths = validate_production_paths(
            source_root=ROOT,
            production_root=args.production_root,
            environment_file=args.compose_env_file,
            data_dir=args.production_root / "data",
            models_dir=args.production_root / "models",
            sentinel_dir=args.production_root / "sentinel",
            prompt_host_root=args.prompt_host_root,
            release_state_dir=state_dir,
        )
        if args.database.resolve() != (
            paths["data_dir"] / "nanobot.db"
        ).resolve():
            raise ProductionPreflightError(
                "生产 SQLite 必须是 NANOBOT_PRODUCTION_ROOT/data/nanobot.db"
            )
        validate_release_source_identity(ROOT, runtime)
        validate_release_artifact_evidence(ROOT, runtime)
    except ProductionPreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    os.environ.update({
        "NANOBOT_PRODUCTION_ENV_FILE": str(paths["environment_file"]),
        "NANOBOT_PRODUCTION_DATA_DIR": str(paths["data_dir"]),
        "NANOBOT_PRODUCTION_MODELS_DIR": str(paths["models_dir"]),
        "NANOBOT_PRODUCTION_SENTINEL_DIR": str(paths["sentinel_dir"]),
        "NANOBOT_PROMPT_HOST_ROOT": str(paths["prompt_host_root"]),
    })
    lock_path = state_dir / "deploy.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有 Runtime 发布正在执行", file=sys.stderr)
            return 2

        state_store = ReleaseStateStore(state_dir)
        deployer = AtomicRuntimeDeployer(
            runner=SubprocessCommandRunner(
                root=ROOT,
                timeout_seconds=args.command_timeout_seconds,
            ),
            state_store=state_store,
            compose_env_file=paths["environment_file"],
            ready_url=args.ready_url,
            system_min_free_bytes=args.system_min_free_bytes,
            pull_reserve_bytes=args.pull_reserve_bytes,
            health_attempts=max(
                1,
                math.ceil(
                    args.health_timeout_seconds
                    / args.health_interval_seconds
                ),
            ),
            health_interval_seconds=args.health_interval_seconds,
            now=lambda: datetime.now(timezone.utc).isoformat(),
        )
        try:
            target_is_current = deployer.target_is_current(target)
            current = None
            if not state_store.pending_path.is_file():
                try:
                    current = state_store.current()
                except DeploymentError:
                    current = None
            backup_required = (
                not target_is_current
                and (
                    current is None
                    or current.runtime_artifact.schema_migration_head
                    != runtime.schema_migration_head
                )
            )
            prompt_required = (
                not target_is_current
                and (
                    current is None
                    or current.runtime_artifact.input_hashes.get(
                        "prompt_defaults"
                    )
                    != runtime.input_hashes.get("prompt_defaults")
                )
            )
            if args.plan_only:
                print(json.dumps(
                    {
                        "schema_version": 1,
                        "runtime_deployment_required": (
                            not target_is_current
                        ),
                        "coordinated_backup_required": backup_required,
                        "prompt_audit_required": prompt_required,
                        "target_release_id": target.release_id,
                        "target_source_sha": (
                            runtime.source.git_full_commit
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ))
                return 0
            if target_is_current:
                print("RUNTIME_DEPLOYMENT_REQUIRED=false")
                print("COORDINATED_BACKUP_REQUIRED=false")
                print("PROMPT_AUDIT_REQUIRED=false")
                print(
                    f"Runtime Release 已处于目标版本：{target.release_id}；"
                    "四个固定服务身份与健康状态一致。"
                )
                return 0

            try:
                validate_pull_disk_gate(
                    system_min_free_bytes=args.system_min_free_bytes,
                    pull_reserve_bytes=args.pull_reserve_bytes,
                )
                if backup_required:
                    if args.backup_dir is None:
                        raise ProductionPreflightError(
                            "目标 Runtime 改变数据库 migration head，必须提供协调备份"
                        )
                    validate_coordinated_backup(
                        backup_dir=args.backup_dir,
                        database_path=args.database,
                        data_root=args.sandbox_data_root,
                        expected_risk_marker=args.backup_risk_marker,
                        max_age_seconds=args.evidence_max_age_seconds,
                    )
                if prompt_required:
                    if args.prompt_audit_receipt is None:
                        raise ProductionPreflightError(
                            "目标 Runtime 改变 Prompt defaults，必须提供 Prompt 审计回执"
                        )
                    validate_prompt_audit_receipt(
                        receipt_path=args.prompt_audit_receipt,
                        prompt_host_root=paths["prompt_host_root"],
                        artifact=runtime,
                        max_age_seconds=args.evidence_max_age_seconds,
                    )
                validate_database_feature_kill_switches(args.database)
            except ProductionPreflightError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            print("RUNTIME_DEPLOYMENT_REQUIRED=true")
            print(
                "COORDINATED_BACKUP_REQUIRED="
                + str(backup_required).lower()
            )
            print(
                "PROMPT_AUDIT_REQUIRED="
                + str(prompt_required).lower()
            )
            result = deployer.deploy(target)
        except AtomicDeploymentError as exc:
            print(str(exc), file=sys.stderr)
            return 1 if exc.rollback_succeeded else 3
        except DeploymentError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    action = "已切换" if result.changed else "已处于目标版本"
    print(
        f"Runtime Release {action}：{result.release_id}；"
        "四个固定服务身份与健康状态一致。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
