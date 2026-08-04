"""超大工具结果到 owner-scoped Artifact 的生产发布适配。"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from core.agent_runtime.contracts import (
    AgentTurnRequest,
    RuntimeArtifactRef,
    RuntimePrincipal,
)
from core.artifact_port import (
    ArtifactStreamPublishRequest,
    SqlAlchemyArtifactPort,
)
from core.database import SessionLocal
from core.sandbox.identity import Principal


async def _single_chunk(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


class SqlAlchemyToolResultArtifactPublisher:
    """每次发布使用独立事务，避免跨越模型调用持有数据库 Session。"""

    async def publish_tool_result(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        payload: bytes,
        media_type: str,
        request: object,
    ) -> RuntimeArtifactRef:
        if not isinstance(request, AgentTurnRequest):
            raise TypeError("工具结果 Artifact request 必须是 AgentTurnRequest")
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("工具结果 Artifact payload 必须是 bytes")
        content = bytes(payload)
        context = request.context
        owner = context.principal
        if not isinstance(owner, RuntimePrincipal):
            raise TypeError("工具结果 Artifact owner 无效")
        principal = Principal(
            platform=owner.platform,
            owner_type=owner.owner_type.value,
            owner_id=owner.owner_id,
        )
        digest = hashlib.sha256(content).hexdigest()
        run_scope = hashlib.sha256(
            context.run_id.encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        call_scope = hashlib.sha256(
            (
                f"{str(tool_name or '').strip()}\0"
                f"{str(tool_call_id or '').strip()}"
            ).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        extension = "json" if media_type == "application/json" else "txt"
        virtual_path = (
            ".nanobot/tool-results/"
            f"{run_scope}/{call_scope}-{digest[:16]}.{extension}"
        )

        db = SessionLocal()
        port: SqlAlchemyArtifactPort | None = None
        try:
            port = SqlAlchemyArtifactPort.from_settings(db)
            workspace = port.workspace_service.ensure_default(principal)
            artifact = await port.publish_stream(ArtifactStreamPublishRequest(
                owner=owner,
                workspace_id=str(workspace.id),
                virtual_path=virtual_path,
                media_type=media_type,
                content=_single_chunk(content),
                content_length=len(content),
                source_run_id=context.run_id,
                source_kind="tool",
                expected_sha256=digest,
                overwrite=False,
            ))
            db.commit()
            return artifact
        except BaseException:
            db.rollback()
            raise
        finally:
            if port is not None:
                port.close()
            db.close()


__all__ = ["SqlAlchemyToolResultArtifactPublisher"]
