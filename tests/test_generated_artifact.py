from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

from core.agent_runtime import RuntimeArtifactRef
from core.agent_runtime.request_scope import runtime_context_scope
from core.generated_artifact import publish_generated_image_artifact
from tests.async_helpers import run_async


class _FakeDb:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _FakeArtifactPort:
    def __init__(self, expected: bytes) -> None:
        self.expected = expected
        self.closed = False
        self.request = None
        self.workspace_service = SimpleNamespace(
            ensure_default=self._ensure_default,
        )

    @staticmethod
    def _ensure_default(principal):
        assert principal.canonical_id == "qq:project:project-image-test"
        return SimpleNamespace(id="workspace-image-test")

    async def publish_stream(self, request):
        content = bytearray()
        async for chunk in request.content:
            content.extend(chunk)
        assert bytes(content) == self.expected
        self.request = request
        digest = hashlib.sha256(self.expected).hexdigest()
        return RuntimeArtifactRef(
            artifact_id="art_" + digest[:48],
            uri="artifact://art_" + digest[:48],
            sha256=digest,
            media_type="image/png",
            size_bytes=len(self.expected),
            version=3,
            source_run_id=request.source_run_id,
        )

    def close(self) -> None:
        self.closed = True


def test_generated_image_uses_explicit_runtime_owner_and_artifact_port(
    monkeypatch,
):
    import core.generated_artifact as generated_artifact

    png = b"\x89PNG\r\n\x1a\n" + b"generated-image"
    encoded = base64.b64encode(png).decode("ascii")
    db = _FakeDb()
    port = _FakeArtifactPort(png)
    monkeypatch.setattr(generated_artifact, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        generated_artifact.SqlAlchemyArtifactPort,
        "from_settings",
        lambda _db: port,
    )

    with runtime_context_scope({
        "chat_type": "task",
        "platform": "qq",
        "owner_type": "project",
        "owner_id": "project-image-test",
        "run_id": "run-image-artifact",
    }):
        result = run_async(publish_generated_image_artifact(
            encoded,
            prompt="不会写入资产或消息历史",
            metadata={"model": "image-model"},
        ))

    assert result["reply_token"].startswith("[artifact:art_")
    assert result["version"] == 3
    assert result["source_run_id"] == "run-image-artifact"
    assert "image_b64" not in result
    assert "saved_path" not in result
    assert port.request.workspace_id == "workspace-image-test"
    assert port.request.virtual_path.startswith(
        ".nanobot/generated-images/"
    )
    assert port.request.expected_sha256 == hashlib.sha256(png).hexdigest()
    assert port.request.source_kind == "tool"
    assert db.committed is True
    assert db.rolled_back is False
    assert db.closed is True
    assert port.closed is True
