"""把模型生成的二进制结果写入 owner Workspace 后发布为 Artifact。"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

from core.agent_runtime import RuntimeOwnerType, RuntimePrincipal
from core.agent_runtime.request_scope import require_current_runtime_context
from core.artifact_port import (
    ArtifactStreamPublishRequest,
    SqlAlchemyArtifactPort,
)
from core.database import SessionLocal
from core.sandbox.identity import derive_principal
from foundation.identity import Principal


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


async def _single_chunk(value: bytes) -> AsyncIterator[bytes]:
    yield value


async def publish_generated_image_artifact(
    image_b64: str,
    *,
    prompt: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """发布模型图片；数据库和宿主路径均不保存 base64。"""

    del prompt, metadata
    try:
        raw = base64.b64decode(str(image_b64 or ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("图片结果不是有效 base64") from exc
    if not raw.startswith(_PNG_MAGIC):
        raise ValueError("图片结果不是有效 PNG")

    context = require_current_runtime_context()
    if context.get("owner_type") and context.get("owner_id"):
        principal = Principal(
            platform=str(context.get("platform") or ""),
            owner_type=str(context.get("owner_type") or ""),
            owner_id=str(context.get("owner_id") or ""),
        )
    else:
        principal = derive_principal(context, group_enabled=True)
    owner = RuntimePrincipal(
        platform=str(principal.platform),
        owner_type=RuntimeOwnerType(str(principal.owner_type)),
        owner_id=str(principal.owner_id),
    )
    run_id = str(context.get("run_id") or "").strip()
    run_scope = hashlib.sha256(
        (run_id or owner.canonical_id).encode("utf-8")
    ).hexdigest()[:16]
    digest = hashlib.sha256(raw).hexdigest()
    virtual_path = (
        f".nanobot/generated-images/{run_scope}/{digest}.png"
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
            media_type="image/png",
            content=_single_chunk(raw),
            content_length=len(raw),
            source_run_id=run_id,
            source_kind="tool",
            expected_sha256=digest,
            overwrite=False,
        ))
        db.commit()
        return {
            "artifact_id": artifact.artifact_id,
            "ref": artifact.uri,
            "content_ref": f"asset://sha256/{artifact.sha256}",
            "sha256": artifact.sha256,
            "mime": artifact.media_type,
            "image_bytes": artifact.size_bytes,
            "version": artifact.version,
            "source_run_id": artifact.source_run_id,
            "reply_token": f"[artifact:{artifact.artifact_id}]",
        }
    except BaseException:
        db.rollback()
        raise
    finally:
        if port is not None:
            port.close()
        db.close()


__all__ = ["publish_generated_image_artifact"]
