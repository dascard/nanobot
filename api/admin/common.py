"""Admin API 共享依赖。"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Mapping
from hmac import compare_digest
from typing import Any, TypeVar

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from config import NANOBOT_ADMIN_TOKEN as CONFIG_ADMIN_TOKEN
from core.admin_audit import (
    AdminAuditError,
    fail_external_admin_audit,
    finalize_external_admin_audit,
    prepare_external_admin_audit,
    stage_admin_audit,
)


_LOGGER = logging.getLogger("nanobot.admin.audit")
_ResultT = TypeVar("_ResultT")


class AuthenticatedAdminPrincipal(str):
    """兼容字符串调用面的类型化 Admin 凭据主体。"""

    _SCOPES = frozenset({"admin:*", "session_goal:approve"})

    def __new__(cls):
        return super().__new__(cls, "admin")

    @property
    def subject(self) -> str:
        return str(self)

    @property
    def scopes(self) -> frozenset[str]:
        return self._SCOPES

    def has_scope(self, scope: str) -> bool:
        normalized = str(scope or "").strip()
        return normalized in self._SCOPES or "admin:*" in self._SCOPES


def _current_admin_token() -> str:
    # 兼容现有测试对 api.admin_routes.NANOBOT_ADMIN_TOKEN 的 monkeypatch。
    admin_routes = sys.modules.get("api.admin_routes")
    if admin_routes is not None and hasattr(admin_routes, "NANOBOT_ADMIN_TOKEN"):
        return str(getattr(admin_routes, "NANOBOT_ADMIN_TOKEN") or "")
    return str(CONFIG_ADMIN_TOKEN or "")


def verify_admin(
    authorization: str = Header(default=""),
) -> AuthenticatedAdminPrincipal:
    token_config = _current_admin_token()
    if not token_config:
        raise HTTPException(status_code=503, detail="Admin token not configured")
    scheme, separator, value = str(authorization or "").partition(" ")
    token = value.strip() if separator and scheme.lower() == "bearer" else ""
    if not token or not compare_digest(token, token_config):
        raise HTTPException(status_code=401, detail="Invalid token")
    return AuthenticatedAdminPrincipal()


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
        stage_admin_audit(
            db,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            detail=detail,
            ip_address=(ip_address or "")[:45],
        )
        db.commit()
    except Exception:
        db.rollback()
        _LOGGER.exception("普通管理审计写入失败，已回滚审计事务")


def stage_audit(
    db: Session,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
    ip_address: str = "",
    *,
    admin_user: str = "admin",
    event_id: str = "",
) -> None:
    """将审计行加入调用方事务，不在内部提交。"""

    stage_admin_audit(
        db,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        detail=detail,
        ip_address=(ip_address or "")[:45],
        admin_user=admin_user,
        event_id=event_id,
    )


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


def stage_audit_request(
    db: Session,
    request: Request,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: dict[str, Any] | None = None,
    *,
    admin_user: str = "admin",
    event_id: str = "",
) -> None:
    """将请求审计加入现有业务事务。"""

    stage_audit(
        db,
        action,
        target_type,
        target_id,
        detail,
        ip_address=client_ip(request),
        admin_user=admin_user,
        event_id=event_id,
    )


def audited_external_action(
    db: Session,
    request: Request,
    *,
    action: str,
    target_type: str,
    target_id: str,
    request_detail: Mapping[str, Any] | None,
    operation: Callable[[], _ResultT],
    result_target_id: Callable[[_ResultT], str],
    result_detail: Callable[[_ResultT], Mapping[str, Any]],
    admin_user: str = "admin",
) -> _ResultT:
    """以写前审计意图包裹文件或外部控制面的治理操作。"""

    try:
        intent = prepare_external_admin_audit(
            db,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=request_detail,
            ip_address=client_ip(request),
            admin_user=admin_user,
        )
    except AdminAuditError as exc:
        raise HTTPException(
            status_code=503,
            detail="治理审计不可用，操作未执行",
        ) from exc
    try:
        result = operation()
    except Exception as exc:
        try:
            fail_external_admin_audit(
                db,
                intent,
                error_code=type(exc).__name__,
            )
        except AdminAuditError:
            _LOGGER.exception(
                "外部治理操作失败后无法终结审计意图 event_id=%s",
                intent.event_id,
            )
        raise
    try:
        resolved_target_id = str(result_target_id(result) or "")
        resolved_detail = dict(result_detail(result))
        finalize_external_admin_audit(
            db,
            intent,
            target_id=resolved_target_id,
            detail=resolved_detail,
        )
    except Exception as exc:
        db.rollback()
        _LOGGER.exception(
            "外部治理操作完成但成功审计未确认 event_id=%s",
            intent.event_id,
        )
        raise HTTPException(
            status_code=500,
            detail="治理操作结果不确定，审计意图待核对",
        ) from exc
    return result
