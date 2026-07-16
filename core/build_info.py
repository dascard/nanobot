"""镜像环境与本地 Git 共用的构建信息解析器。"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BuildInfo:
    commit: str
    full_commit: str
    branch: str
    commit_date: str
    dirty: bool | None
    display: str

    def as_dict(self) -> dict[str, object]:
        return {
            "commit": self.commit,
            "full_commit": self.full_commit,
            "branch": self.branch,
            "commit_date": self.commit_date,
            "dirty": self.dirty,
            "display": self.display,
        }


def _parse_dirty(value: str | None) -> bool | None:
    normalized = str(value or "").strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def resolve_build_info(
    *,
    project_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    logger: logging.Logger | None = None,
) -> BuildInfo:
    """逐字段优先读取镜像元数据，仅为缺失字段查询本地 Git。"""

    env = os.environ if environ is None else environ
    root = project_root or Path(__file__).resolve().parents[1]

    def git(args: list[str]) -> str | None:
        try:
            return subprocess.check_output(
                ["git", *args],
                cwd=root,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            ).strip()
        except Exception as exc:
            if logger is not None:
                logger.debug(
                    "[BuildInfo] git probe failed command=%s error_type=%s",
                    args[0] if args else "unknown",
                    type(exc).__name__,
                )
            return None

    commit = str(env.get("NANOBOT_GIT_COMMIT") or "").strip()
    full_commit = str(env.get("NANOBOT_GIT_FULL_COMMIT") or "").strip()
    branch = str(env.get("NANOBOT_GIT_BRANCH") or "").strip()
    commit_date = str(env.get("NANOBOT_GIT_COMMIT_DATE") or "").strip()

    if not full_commit:
        full_commit = git(["rev-parse", "HEAD"]) or ""
    if not commit:
        commit = git(["rev-parse", "--short", "HEAD"]) or ""
    if not commit and full_commit:
        commit = full_commit[:7]
    if not branch:
        branch = git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    if not commit_date:
        commit_date = git(["log", "-1", "--format=%ci", "--date=iso-strict"]) or ""

    dirty_raw = env.get("NANOBOT_GIT_DIRTY")
    if dirty_raw is None or not str(dirty_raw).strip():
        status = git(
            [
                "status",
                "--porcelain",
                "--untracked-files=no",
                "--",
                ".",
                ":(exclude)data",
                ":(exclude).claude",
                ":(exclude)sentinel/model.safetensors",
                ":(exclude).env",
                ":(exclude).vscode",
                ":(exclude).idea",
                ":(exclude)webui/node_modules",
            ]
        )
        dirty = None if status is None else bool(status)
    else:
        dirty = _parse_dirty(str(dirty_raw))

    commit = commit or "unknown"
    return BuildInfo(
        commit=commit,
        full_commit=full_commit,
        branch=branch,
        commit_date=commit_date,
        dirty=dirty,
        display=f"{commit}{'-dirty' if dirty and commit != 'unknown' else ''}",
    )
