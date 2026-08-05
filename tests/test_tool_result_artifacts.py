from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.agent_runtime import (
    AgentTurnRequest,
    RequestRuntimeContext,
    RuntimeActor,
    RuntimeActorType,
    RuntimeArtifactRef,
    RuntimeChatType,
    RuntimeOwnerType,
    RuntimePrincipal,
)
from core.tool_result_artifacts import SqlAlchemyToolResultArtifactPublisher


def _request() -> AgentTurnRequest:
    return AgentTurnRequest(
        context=RequestRuntimeContext(
            request_id="request-artifact-1",
            agent_id="test.agent",
            principal=RuntimePrincipal(
                platform="qq",
                owner_type=RuntimeOwnerType.USER,
                owner_id="10001",
            ),
            session_id="private_10001",
            chat_type=RuntimeChatType.PRIVATE,
            trace_id="trace-artifact-1",
            run_id="run-artifact-1",
            turn_id="turn-artifact-1",
            correlation_id="correlation-artifact-1",
            actor=RuntimeActor(RuntimeActorType.USER, "10001"),
            deadline_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
        ),
        content="测试",
    )


class _Session:
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


class _WorkspaceService:
    def __init__(self) -> None:
        self.principals = []

    def ensure_default(self, principal):
        self.principals.append(principal)
        return SimpleNamespace(id="workspace-1")


class _ArtifactPort:
    def __init__(self) -> None:
        self.workspace_service = _WorkspaceService()
        self.requests = []
        self.closed = False

    async def publish_stream(self, request):
        self.requests.append(request)
        payload = bytearray()
        async for chunk in request.content:
            payload.extend(chunk)
        return RuntimeArtifactRef(
            artifact_id="art_tool_1",
            uri="artifact://art_tool_1",
            sha256=hashlib.sha256(payload).hexdigest(),
            media_type=request.media_type,
            size_bytes=len(payload),
            source_run_id=request.source_run_id,
        )

    def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_tool_result_artifact_publisher_uses_owner_workspace_and_transaction(
    monkeypatch,
):
    import core.tool_result_artifacts as module

    session = _Session()
    port = _ArtifactPort()
    monkeypatch.setattr(module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        module.SqlAlchemyArtifactPort,
        "from_settings",
        lambda _db: port,
    )
    payload = b'{"large":"result"}'

    artifact = await SqlAlchemyToolResultArtifactPublisher().publish_tool_result(
        tool_name="../../web_search",
        tool_call_id="call/../../1",
        payload=payload,
        media_type="application/json",
        request=_request(),
    )

    assert artifact.uri == "artifact://art_tool_1"
    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True
    assert port.closed is True
    assert port.workspace_service.principals[0].owner_id == "10001"
    publish_request = port.requests[0]
    assert publish_request.workspace_id == "workspace-1"
    assert publish_request.source_run_id == "run-artifact-1"
    assert publish_request.source_kind == "tool"
    assert publish_request.virtual_path.startswith(".nanobot/tool-results/")
    assert ".." not in publish_request.virtual_path
    assert publish_request.expected_sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_tool_result_artifact_publisher_rolls_back_failed_publish(monkeypatch):
    import core.tool_result_artifacts as module

    session = _Session()
    port = _ArtifactPort()

    async def fail_publish(_request):
        raise RuntimeError("sandboxd unavailable")

    port.publish_stream = fail_publish
    monkeypatch.setattr(module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        module.SqlAlchemyArtifactPort,
        "from_settings",
        lambda _db: port,
    )

    with pytest.raises(RuntimeError, match="sandboxd unavailable"):
        await SqlAlchemyToolResultArtifactPublisher().publish_tool_result(
            tool_name="web_search",
            tool_call_id="call-1",
            payload=b"large",
            media_type="text/plain; charset=utf-8",
            request=_request(),
        )

    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True
    assert port.closed is True
