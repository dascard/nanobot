"""MCP server 配置的严格解析、CAS 和原子全量替换。"""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.db.models.mcp import McpConfigurationStateRow, McpServerRow
from core.mcp.contracts import (
    McpConfigurationConflict,
    McpConfigurationSnapshot,
    McpControlPlaneError,
    McpSecretReference,
    McpServerConfig,
)


def _json_array(value: object, name: str) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise McpControlPlaneError(f"{name} 不是合法 JSON") from exc
    if not isinstance(parsed, list):
        raise McpControlPlaneError(f"{name} 必须是数组")
    return parsed


def _safe_server_id(value: object) -> str:
    raw = str(value or "").strip()
    if raw and len(raw) <= 64 and all(
        char.isalnum() or char in "._-" for char in raw
    ):
        return raw
    return "invalid-server"


def _from_row(row: McpServerRow) -> McpServerConfig:
    refs = _json_array(row.secret_refs_json, "mcp.secret_refs_json")
    config = McpServerConfig(
        server_id=row.server_id,
        display_name=row.display_name,
        transport=row.transport,
        enabled=bool(row.enabled),
        endpoint=row.endpoint,
        command=row.command,
        args=tuple(str(item) for item in _json_array(row.args_json, "mcp.args_json")),
        cwd=row.cwd,
        connect_timeout_seconds=row.connect_timeout_seconds,
        request_timeout_seconds=row.request_timeout_seconds,
        sse_read_timeout_seconds=row.sse_read_timeout_seconds,
        reconnect_attempts=row.reconnect_attempts,
        max_tools=row.max_tools,
        auth_mode=row.auth_mode,
        oauth_token_url=row.oauth_token_url,
        oauth_scopes=tuple(
            str(item)
            for item in _json_array(row.oauth_scopes_json, "mcp.oauth_scopes_json")
        ),
        secret_refs=tuple(
            McpSecretReference(
                binding=(item.get("binding") if isinstance(item, dict) else ""),
                secret_id=(item.get("secret_id") if isinstance(item, dict) else ""),
            )
            for item in refs
        ),
    )
    if config.config_sha256 != str(row.config_sha256 or ""):
        raise McpControlPlaneError("MCP server config_sha256 漂移")
    return config


def _row(config: McpServerConfig, *, created_at: datetime | None = None) -> McpServerRow:
    now = datetime.now()
    return McpServerRow(
        server_id=config.server_id,
        display_name=config.display_name,
        transport=config.transport.value,
        enabled=config.enabled,
        endpoint=config.endpoint,
        command=config.command,
        args_json=json.dumps(list(config.args), ensure_ascii=False, separators=(",", ":")),
        cwd=config.cwd,
        connect_timeout_seconds=config.connect_timeout_seconds,
        request_timeout_seconds=config.request_timeout_seconds,
        sse_read_timeout_seconds=config.sse_read_timeout_seconds,
        reconnect_attempts=config.reconnect_attempts,
        max_tools=config.max_tools,
        auth_mode=config.auth_mode.value,
        oauth_token_url=config.oauth_token_url,
        oauth_scopes_json=json.dumps(
            list(config.oauth_scopes), ensure_ascii=False, separators=(",", ":")
        ),
        secret_refs_json=json.dumps(
            [item.to_dict() for item in config.secret_refs],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        config_sha256=config.config_sha256,
        created_at=created_at or now,
        updated_at=now,
    )


class McpConfigurationService:
    """以一个事务替换完整 server 集合，不发布部分候选。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def current_revision(self) -> int:
        state = self.db.get(McpConfigurationStateRow, 1)
        return int(state.revision) if state is not None else 0

    def snapshot(self) -> McpConfigurationSnapshot:
        state = self.db.get(McpConfigurationStateRow, 1)
        revision = int(state.revision) if state is not None else 0
        servers: list[McpServerConfig] = []
        diagnostics: list[dict[str, str]] = []
        rows = self.db.query(McpServerRow).order_by(McpServerRow.server_id.asc()).all()
        for row in rows:
            try:
                servers.append(_from_row(row))
            except (McpControlPlaneError, TypeError, ValueError):
                diagnostics.append({
                    "server_id": _safe_server_id(row.server_id),
                    "code": "invalid_persisted_config",
                })
        snapshot = McpConfigurationSnapshot.build(
            revision,
            tuple(servers),
            diagnostics=tuple(diagnostics),
        )
        if state is not None and snapshot.sha256 != str(state.registry_sha256 or ""):
            diagnostics.append({
                "server_id": "configuration",
                "code": "registry_sha256_drift",
            })
            snapshot = McpConfigurationSnapshot.build(
                revision,
                tuple(servers),
                diagnostics=tuple(diagnostics),
            )
        return snapshot

    def replace_all(
        self,
        servers: tuple[McpServerConfig, ...],
        *,
        expected_revision: int,
        actor_id: str,
    ) -> McpConfigurationSnapshot:
        if (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            raise McpControlPlaneError("expected_revision 必须是非负整数")
        normalized = tuple(servers)
        if len(normalized) > 64:
            raise McpControlPlaneError("MCP server 总数不能超过 64")
        if any(not isinstance(item, McpServerConfig) for item in normalized):
            raise McpControlPlaneError("servers 包含无效配置")
        identities = [item.server_id for item in normalized]
        if len(identities) != len(set(identities)):
            raise McpControlPlaneError("MCP server_id 不能重复")
        current_revision = self.current_revision()
        if current_revision != expected_revision:
            raise McpConfigurationConflict(
                f"MCP 配置 revision 已变化：期望 {expected_revision}，当前 {current_revision}"
            )
        next_revision = current_revision + 1
        candidate = McpConfigurationSnapshot.build(next_revision, normalized)
        existing_created_at = {
            row.server_id: row.created_at
            for row in self.db.query(McpServerRow).all()
        }
        self.db.query(McpServerRow).delete(synchronize_session=False)
        for config in candidate.servers:
            self.db.add(_row(
                config,
                created_at=existing_created_at.get(config.server_id),
            ))
        state = self.db.get(McpConfigurationStateRow, 1)
        if state is None:
            state = McpConfigurationStateRow(
                id=1,
                revision=next_revision,
                registry_sha256=candidate.sha256,
                updated_by=str(actor_id or "admin")[:255],
            )
            self.db.add(state)
            try:
                self.db.flush()
            except IntegrityError as exc:
                raise McpConfigurationConflict(
                    "MCP 配置首次发布发生并发冲突"
                ) from exc
        else:
            changed = self.db.execute(
                update(McpConfigurationStateRow)
                .where(
                    McpConfigurationStateRow.id == 1,
                    McpConfigurationStateRow.revision == expected_revision,
                )
                .values(
                    revision=next_revision,
                    registry_sha256=candidate.sha256,
                    updated_by=str(actor_id or "admin")[:255],
                    updated_at=datetime.now(),
                )
            )
            if int(changed.rowcount or 0) != 1:
                raise McpConfigurationConflict("MCP 配置并发更新冲突")
        self.db.flush()
        return candidate

    def set_enabled(
        self,
        server_id: str,
        *,
        enabled: bool,
        expected_revision: int,
        actor_id: str,
    ) -> McpConfigurationSnapshot:
        if not isinstance(enabled, bool):
            raise McpControlPlaneError("enabled 必须是 bool")
        current = self.snapshot()
        if current.revision != expected_revision:
            raise McpConfigurationConflict(
                f"MCP 配置 revision 已变化：期望 {expected_revision}，当前 {current.revision}"
            )
        found = False
        updated: list[McpServerConfig] = []
        for config in current.servers:
            if config.server_id != str(server_id or "").strip():
                updated.append(config)
                continue
            found = True
            payload = config.to_dict()
            payload["enabled"] = enabled
            payload["args"] = tuple(payload["args"])
            payload["oauth_scopes"] = tuple(payload["oauth_scopes"])
            payload["secret_refs"] = tuple(
                McpSecretReference(**item) for item in payload["secret_refs"]
            )
            updated.append(McpServerConfig(**payload))
        if not found:
            raise McpControlPlaneError("MCP server 不存在")
        return self.replace_all(
            tuple(updated),
            expected_revision=expected_revision,
            actor_id=actor_id,
        )


__all__ = ["McpConfigurationService"]
