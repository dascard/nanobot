"""Agent Skills 受管版本、绑定和可逆生命周期管理 API。"""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from api.admin.common import audit_request, verify_admin
from core.database import get_db
from core.skills import (
    SkillBundleFile,
    SkillContractError,
    SkillLifecycleError,
    SkillLifecycleService,
    SkillNotFoundError,
    SkillScopeTarget,
    SkillVersionConflictError,
    default_bundled_skill_catalog,
    parse_skill_bundle,
)


router = APIRouter(prefix="/skills", dependencies=[Depends(verify_admin)])
ManagedScope = Literal["project", "agent", "user"]


class SkillResourceUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(min_length=1, max_length=3 * 1024 * 1024)
    media_type: str = Field(default="", max_length=128)


class SkillInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ManagedScope
    scope_key: str = Field(min_length=1, max_length=255)
    skill_md: str = Field(min_length=1, max_length=512 * 1024)
    resources: list[SkillResourceUpload] = Field(default_factory=list, max_length=128)
    source_label: str = Field(default="manual-upload", min_length=1, max_length=255)
    trusted_source: bool
    pin: bool = True
    expected_generation: int | None = Field(default=None, ge=1)


class SkillBindingActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: ManagedScope
    scope_key: str = Field(min_length=1, max_length=255)
    skill_name: str = Field(min_length=1, max_length=64)
    expected_generation: int = Field(ge=1)


class SkillPinRequest(SkillBindingActionRequest):
    pinned: bool


class SkillUpgradeRequest(SkillBindingActionRequest):
    target_version: str = Field(min_length=1, max_length=64)


def _target(scope: ManagedScope, scope_key: str) -> SkillScopeTarget:
    return SkillScopeTarget(scope, scope_key)


def _resource(item: SkillResourceUpload) -> SkillBundleFile:
    try:
        content = base64.b64decode(item.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SkillContractError(f"资源 {item.path} 不是严格 base64") from exc
    return SkillBundleFile(item.path, content, item.media_type)


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, SkillNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, SkillVersionConflictError):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, SkillContractError):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, SkillLifecycleError):
        raise HTTPException(409, str(exc)) from exc
    raise exc


@router.get("")
def list_skills(
    scope: ManagedScope | None = Query(default=None),
    scope_key: str = Query(default="", max_length=255),
    skill_name: str = Query(default="", max_length=64),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        if bool(scope) != bool(scope_key):
            raise SkillContractError("scope 与 scope_key 必须同时提供")
        target = _target(scope, scope_key) if scope is not None else None
        service = SkillLifecycleService(db)
        bundled = default_bundled_skill_catalog()
        return {
            "bindings": [
                item.to_dict() for item in service.list_bindings(target=target)
            ],
            "versions": [
                item.to_dict()
                for item in service.list_versions(
                    target=target,
                    skill_name=skill_name,
                )
            ],
            "bundled": [item.entry.to_dict() for item in bundled.records()],
            "diagnostics": list(bundled.diagnostics),
        }
    except (SkillContractError, SkillLifecycleError) as exc:
        _raise_api_error(exc)
        raise AssertionError("unreachable")


@router.post("/install")
def install_skill(
    body: SkillInstallRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        bundle = parse_skill_bundle(
            body.skill_md.encode("utf-8"),
            files=tuple(_resource(item) for item in body.resources),
        )
        snapshot = SkillLifecycleService(db).install(
            _target(body.scope, body.scope_key),
            bundle,
            actor_id="admin",
            source_label=body.source_label,
            trusted_source=body.trusted_source,
            pin=body.pin,
            expected_generation=body.expected_generation,
        )
        db.commit()
        audit_request(
            db,
            request,
            "skill.install",
            "skill_binding",
            snapshot.binding_id,
            {
                "scope": body.scope,
                "scope_key": body.scope_key,
                "skill_name": bundle.name,
                "version": bundle.version,
                "bundle_sha256": bundle.bundle_sha256,
            },
        )
        return snapshot.to_dict()
    except (SkillContractError, SkillLifecycleError) as exc:
        db.rollback()
        _raise_api_error(exc)
        raise AssertionError("unreachable")


def _action_result(
    db: Session,
    request: Request,
    body: SkillBindingActionRequest,
    action: str,
    operation,
) -> dict[str, object]:
    try:
        snapshot = operation(
            _target(body.scope, body.scope_key),
            body.skill_name,
            expected_generation=body.expected_generation,
            actor_id="admin",
        )
        db.commit()
        audit_request(
            db,
            request,
            f"skill.{action}",
            "skill_binding",
            snapshot.binding_id,
            {
                "scope": body.scope,
                "scope_key": body.scope_key,
                "skill_name": body.skill_name,
                "generation": snapshot.generation,
            },
        )
        return snapshot.to_dict()
    except (SkillContractError, SkillLifecycleError) as exc:
        db.rollback()
        _raise_api_error(exc)
        raise AssertionError("unreachable")


@router.post("/pin")
def pin_skill(
    body: SkillPinRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    service = SkillLifecycleService(db)
    return _action_result(
        db,
        request,
        body,
        "pin" if body.pinned else "unpin",
        lambda target, name, **kwargs: service.set_pinned(
            target,
            name,
            pinned=body.pinned,
            **kwargs,
        ),
    )


@router.post("/upgrade")
def upgrade_skill(
    body: SkillUpgradeRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    service = SkillLifecycleService(db)
    return _action_result(
        db,
        request,
        body,
        "upgrade",
        lambda target, name, **kwargs: service.upgrade(
            target,
            name,
            body.target_version,
            **kwargs,
        ),
    )


@router.post("/rollback")
def rollback_skill(
    body: SkillBindingActionRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return _action_result(
        db,
        request,
        body,
        "rollback",
        SkillLifecycleService(db).rollback,
    )


@router.post("/uninstall")
def uninstall_skill(
    body: SkillBindingActionRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return _action_result(
        db,
        request,
        body,
        "uninstall",
        SkillLifecycleService(db).uninstall,
    )


__all__ = ["router"]
