"""MCP 健康、发现和调用的无正文诊断投影。"""

from __future__ import annotations

from datetime import datetime
import re

from sqlalchemy.orm import Session

from core.db.models.mcp import McpDiagnosticRow
from core.mcp.contracts import McpClientFailure, McpServerConfig


_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_.-]")


def _safe(value: object, *, maximum: int) -> str:
    return _SAFE_TOKEN.sub("_", str(value or ""))[:maximum]


class McpDiagnosticService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_success(
        self,
        config: McpServerConfig,
        *,
        operation: str,
        latency_ms: int,
        tool_count: int,
    ) -> None:
        self.db.add(McpDiagnosticRow(
            server_id=config.server_id,
            config_sha256=config.config_sha256,
            transport=config.transport.value,
            operation=_safe(operation, maximum=16),
            status="healthy",
            latency_ms=max(0, int(latency_ms)),
            tool_count=max(0, int(tool_count)),
            occurred_at=datetime.now(),
        ))

    def record_failure(
        self,
        config: McpServerConfig,
        failure: McpClientFailure,
        *,
        operation: str,
    ) -> None:
        self.db.add(McpDiagnosticRow(
            server_id=config.server_id,
            config_sha256=config.config_sha256,
            transport=config.transport.value,
            operation=_safe(operation, maximum=16),
            status="failed",
            error_code=_safe(failure.code, maximum=64),
            error_type=_safe(failure.error_type, maximum=128),
            retryable=failure.retryable,
            ambiguous=failure.ambiguous,
            latency_ms=failure.latency_ms,
            tool_count=0,
            occurred_at=datetime.now(),
        ))

    def list(
        self,
        *,
        server_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, object]]:
        query = self.db.query(McpDiagnosticRow)
        normalized_server_id = str(server_id or "").strip()
        if normalized_server_id:
            query = query.filter(McpDiagnosticRow.server_id == normalized_server_id)
        rows = query.order_by(
            McpDiagnosticRow.occurred_at.desc(),
            McpDiagnosticRow.id.desc(),
        ).limit(max(1, min(int(limit), 500))).all()
        return [
            {
                "id": row.id,
                "server_id": row.server_id,
                "config_sha256": row.config_sha256,
                "transport": row.transport,
                "operation": row.operation,
                "status": row.status,
                "error_code": row.error_code,
                "error_type": row.error_type,
                "retryable": bool(row.retryable),
                "ambiguous": bool(row.ambiguous),
                "latency_ms": int(row.latency_ms),
                "tool_count": int(row.tool_count),
                "occurred_at": row.occurred_at.isoformat(),
            }
            for row in rows
        ]


__all__ = ["McpDiagnosticService"]
