"""Admin 基础与系统信息路由。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.admin.common import verify_admin


router = APIRouter()
_VERSION_CACHE: dict | None = None
logger = logging.getLogger("nanobot.admin")


@router.get("/me")
def admin_me(_auth=Depends(verify_admin)):
    return {"ok": True, "user": "admin"}


@router.get("/version")
def admin_version(_auth=Depends(verify_admin)):
    global _VERSION_CACHE
    if _VERSION_CACHE is not None:
        return _VERSION_CACHE

    from core.build_info import resolve_build_info

    _VERSION_CACHE = resolve_build_info(logger=logger).as_dict()
    return _VERSION_CACHE


@router.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}
