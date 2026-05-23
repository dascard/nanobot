"""Admin API 共享依赖。"""

from __future__ import annotations

import json
import sys
from hmac import compare_digest
from typing import Any

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from config import NANOBOT_ADMIN_TOKEN as CONFIG_ADMIN_TOKEN
from core.database import AdminAuditLog


def _current_admin_token() -> str:
    # 兼容现有测试对 api.admin_routes.NANOBOT_ADMIN_TOKEN 的 monkeypatch。
    admin_routes = sys.modules.get("api.admin_routes")
    if admin_routes is not None and hasattr(admin_routes, "NANOBOT_ADMIN_TOKEN"):
        return str(getattr(admin_routes, "NANOBOT_ADMIN_TOKEN") or "")
    return str(CONFIG_ADMIN_TOKEN or "")


def verify_admin(authorization: str = Header(default="")) -> str:
    token_config = _current_admin_token()
    if not token_config:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    token = authorization.replace("Bearer ", "").strip()
    if not token or not compare_digest(token, token_config):
        raise HTTPException(status_code=401, detail="Invalid token")
    return "admin"


def client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()[:45]
    client = getattr(request, "client", None)
    if client and hasattr(client, "host"):
        return str(client.host)[:45]
    return ""


def audit(
    db: Session,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
    ip_address: str = "",
) -> None:
    try:
        db.add(AdminAuditLog(
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
            ip_address=(ip_address or "")[:45],
        ))
        db.commit()
    except Exception:
        pass


def audit_request(
    db: Session,
    request: Request,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    audit(
        db,
        action,
        target_type,
        target_id,
        detail,
        ip_address=client_ip(request),
    )
