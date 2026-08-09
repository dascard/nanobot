"""MCP 原子配置、秘密引用、健康检查和诊断管理 API。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.orm import Session

from api.admin.common import stage_audit_request, verify_admin
from core.database import get_db
from core.mcp import (
    DEFAULT_MCP_CATALOG_CACHE,
    McpConfigurationConflict,
    McpConfigurationService,
    McpControlPlaneError,
    McpDiagnosticService,
    McpRuntimeService,
    McpSecretReference,
    McpSecretService,
    McpSecretUnavailable,
    McpServerConfig,
)


router = APIRouter(prefix="/mcp", dependencies=[Depends(verify_admin)])


def _session_factory() -> Session:
    """延迟读取会话工厂，保持测试覆写与运行时配置一致。"""

    from core import database

    return database.SessionLocal()


def _mcp_client():
    try:
        from clients.mcp import McpSdkClient
    except ModuleNotFoundError as exc:
        raise HTTPException(503, "MCP transport SDK 未安装") from exc

    return McpSdkClient()


class McpSecretReferenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: str = Field(min_length=1, max_length=160)
    secret_id: str = Field(min_length=1, max_length=128)


class McpServerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    transport: Literal["stdio", "sse", "http"]
    enabled: bool = False
    endpoint: str = Field(default="", max_length=2048)
    command: str = Field(default="", max_length=2048)
    args: list[str] = Field(default_factory=list, max_length=128)
    cwd: str = Field(default="", max_length=2048)
    connect_timeout_seconds: float = Field(default=10.0, ge=0.1, le=120)
    request_timeout_seconds: float = Field(default=60.0, ge=0.1, le=1800)
    sse_read_timeout_seconds: float = Field(default=300.0, ge=1, le=3600)
    reconnect_attempts: int = Field(default=1, ge=0, le=5)
    max_tools: int = Field(default=20, ge=1, le=100)
    auth_mode: Literal["none", "bearer", "oauth_client_credentials"] = "none"
    oauth_token_url: str = Field(default="", max_length=2048)
    oauth_scopes: list[str] = Field(default_factory=list, max_length=32)
    secret_refs: list[McpSecretReferenceBody] = Field(
        default_factory=list,
        max_length=32,
    )


class McpConfigurationReplaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    servers: list[McpServerBody] = Field(default_factory=list, max_length=64)


class McpEnabledBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    enabled: bool


class McpSecretWriteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["replace", "clear"]
    value: SecretStr | None = None


def _config(body: McpServerBody) -> McpServerConfig:
    return McpServerConfig(
        server_id=body.server_id,
        display_name=body.display_name,
        transport=body.transport,
        enabled=body.enabled,
        endpoint=body.endpoint,
        command=body.command,
        args=tuple(body.args),
        cwd=body.cwd,
        connect_timeout_seconds=body.connect_timeout_seconds,
        request_timeout_seconds=body.request_timeout_seconds,
        sse_read_timeout_seconds=body.sse_read_timeout_seconds,
        reconnect_attempts=body.reconnect_attempts,
        max_tools=body.max_tools,
        auth_mode=body.auth_mode,
        oauth_token_url=body.oauth_token_url,
        oauth_scopes=tuple(body.oauth_scopes),
        secret_refs=tuple(
            McpSecretReference(item.binding, item.secret_id)
            for item in body.secret_refs
        ),
    )


def _public_configuration(db: Session) -> dict[str, object]:
    snapshot = McpConfigurationService(db).snapshot()
    configured_secrets = McpSecretService(db).configured_ids()
    servers: list[dict[str, object]] = []
    for config in snapshot.servers:
        payload = config.to_dict()
        payload["config_sha256"] = config.config_sha256
        payload["secret_refs"] = [
            {
                **ref.to_dict(),
                "configured": ref.secret_id in configured_secrets,
            }
            for ref in config.secret_refs
        ]
        servers.append(payload)
    return {
        "revision": snapshot.revision,
        "registry_sha256": snapshot.sha256,
        "servers": servers,
        "diagnostics": [dict(item) for item in snapshot.diagnostics],
    }


def _raise(exc: Exception) -> None:
    if isinstance(exc, McpConfigurationConflict):
        raise HTTPException(409, str(exc)) from exc
    if isinstance(exc, McpSecretUnavailable):
        raise HTTPException(503, str(exc)) from exc
    if isinstance(exc, McpControlPlaneError):
        raise HTTPException(422, str(exc)) from exc
    raise exc


@router.get("")
def get_mcp_configuration(db: Session = Depends(get_db)) -> dict[str, object]:
    return _public_configuration(db)


@router.put("")
def replace_mcp_configuration(
    body: McpConfigurationReplaceBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        servers = tuple(_config(item) for item in body.servers)
        snapshot = McpConfigurationService(db).replace_all(
            servers,
            expected_revision=body.expected_revision,
            actor_id="admin",
        )
        stage_audit_request(
            db,
            request,
            "replace_mcp_configuration",
            "mcp_configuration",
            str(snapshot.revision),
            {
                "server_count": len(snapshot.servers),
                "enabled_count": sum(
                    1 for item in snapshot.servers if item.enabled
                ),
                "registry_sha256": snapshot.sha256,
            },
        )
        db.commit()
    except (McpControlPlaneError, TypeError, ValueError) as exc:
        db.rollback()
        _raise(exc)
        raise AssertionError("unreachable")
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "MCP 治理操作未提交") from exc
    DEFAULT_MCP_CATALOG_CACHE.clear()
    return _public_configuration(db)


@router.patch("/servers/{server_id}/enabled")
def set_mcp_server_enabled(
    server_id: str,
    body: McpEnabledBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        snapshot = McpConfigurationService(db).set_enabled(
            server_id,
            enabled=body.enabled,
            expected_revision=body.expected_revision,
            actor_id="admin",
        )
        stage_audit_request(
            db,
            request,
            "set_mcp_server_enabled",
            "mcp_server",
            server_id,
            {"enabled": body.enabled, "revision": snapshot.revision},
        )
        db.commit()
    except (McpControlPlaneError, TypeError, ValueError) as exc:
        db.rollback()
        _raise(exc)
        raise AssertionError("unreachable")
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "MCP 治理操作未提交") from exc
    DEFAULT_MCP_CATALOG_CACHE.clear()
    return _public_configuration(db)


@router.put("/secrets/{secret_id}")
def write_mcp_secret(
    secret_id: str,
    body: McpSecretWriteBody,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        service = McpSecretService(db)
        if body.action == "replace":
            value = body.value.get_secret_value() if body.value is not None else ""
            if not value:
                raise McpControlPlaneError("replace 必须提供非空 secret value")
            service.replace(secret_id, value)
            configured = True
        else:
            if body.value is not None and body.value.get_secret_value():
                raise McpControlPlaneError("clear 不能同时提交 secret value")
            service.clear(secret_id)
            configured = False
        stage_audit_request(
            db,
            request,
            "write_mcp_secret",
            "mcp_secret_ref",
            secret_id,
            {"action": body.action},
        )
        db.commit()
    except (McpControlPlaneError, TypeError, ValueError) as exc:
        db.rollback()
        _raise(exc)
        raise AssertionError("unreachable")
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, "MCP 治理操作未提交") from exc
    DEFAULT_MCP_CATALOG_CACHE.clear()
    return {"secret_id": secret_id, "configured": configured}


@router.post("/servers/{server_id}/health")
async def check_mcp_server_health(
    server_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = await McpRuntimeService(
            db,
            client=_mcp_client(),
            session_factory=_session_factory,
        ).health(server_id)
        db.commit()
        return result
    except KeyError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc
    except (McpControlPlaneError, TypeError, ValueError) as exc:
        db.rollback()
        _raise(exc)
        raise AssertionError("unreachable")


@router.post("/health")
async def check_all_mcp_servers_health(
    db: Session = Depends(get_db),
) -> dict[str, object]:
    snapshot = McpConfigurationService(db).snapshot()
    service = McpRuntimeService(
        db,
        client=_mcp_client(),
        session_factory=_session_factory,
    )
    results = []
    for config in snapshot.servers:
        results.append(await service.health(config.server_id))
    db.commit()
    return {"revision": snapshot.revision, "servers": results}


@router.get("/diagnostics")
def list_mcp_diagnostics(
    server_id: str = Query(default="", max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return {
        "diagnostics": McpDiagnosticService(db).list(
            server_id=server_id,
            limit=limit,
        )
    }


__all__ = [
    "McpConfigurationReplaceBody",
    "McpEnabledBody",
    "McpSecretWriteBody",
    "McpServerBody",
    "router",
]
