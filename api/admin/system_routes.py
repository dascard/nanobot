"""Admin 基础与系统信息路由。"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime

from fastapi import APIRouter, Depends

from api.admin.common import verify_admin


router = APIRouter()
_VERSION_CACHE: dict | None = None


@router.get("/me")
def admin_me(_auth=Depends(verify_admin)):
    return {"ok": True, "user": "admin"}


@router.get("/version")
def admin_version(_auth=Depends(verify_admin)):
    global _VERSION_CACHE
    if _VERSION_CACHE is not None:
        return _VERSION_CACHE

    commit = os.environ.get("NANOBOT_GIT_COMMIT") or ""
    branch = os.environ.get("NANOBOT_GIT_BRANCH") or ""
    commit_date = os.environ.get("NANOBOT_GIT_COMMIT_DATE") or ""
    dirty_raw = os.environ.get("NANOBOT_GIT_DIRTY") or ""

    if not commit or not branch:
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        def _git(args: list[str]) -> str | None:
            try:
                return subprocess.check_output(
                    ["git", *args],
                    cwd=base,
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                ).strip()
            except Exception:
                return None

        commit = commit or _git(["rev-parse", "--short", "HEAD"]) or "unknown"
        branch = branch or _git(["rev-parse", "--abbrev-ref", "HEAD"]) or ""
        commit_date = commit_date or _git(["log", "-1", "--format=%ci", "--date=iso-strict"]) or ""

        if not dirty_raw:
            status = _git([
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
            ])
            if status is None:
                dirty_raw = "null"
            elif status:
                dirty_raw = "true"
            else:
                dirty_raw = "false"

    if dirty_raw == "true":
        dirty = True
    elif dirty_raw == "false":
        dirty = False
    else:
        dirty = None

    _VERSION_CACHE = {
        "commit": commit or "unknown",
        "full_commit": os.environ.get("NANOBOT_GIT_FULL_COMMIT", "") or "",
        "branch": branch or "",
        "commit_date": commit_date or "",
        "dirty": dirty,
        "display": f"{commit}{'-dirty' if dirty and commit != 'unknown' else ''}",
    }
    return _VERSION_CACHE


@router.get("/health")
def health():
    return {"ok": True, "time": datetime.now().isoformat()}
