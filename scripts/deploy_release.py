#!/usr/bin/env python3
"""使用 ReleaseManifest 原子切换四个固定 Runtime 服务。"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import fcntl
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
        default=Path("data/release-state"),
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
    ):
        print("部署超时参数必须大于 0", file=sys.stderr)
        return 2

    state_dir = (
        args.state_dir
        if args.state_dir.is_absolute()
        else ROOT / args.state_dir
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / "deploy.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("已有 Runtime 发布正在执行", file=sys.stderr)
            return 2

        deployer = AtomicRuntimeDeployer(
            runner=SubprocessCommandRunner(
                root=ROOT,
                timeout_seconds=args.command_timeout_seconds,
            ),
            state_store=ReleaseStateStore(state_dir),
            ready_url=args.ready_url,
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
