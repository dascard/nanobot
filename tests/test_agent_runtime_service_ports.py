from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.agent_runtime import (
    ArtifactPort,
    CheckpointStore,
    InMemoryArtifactPort,
    InMemoryCheckpointStore,
    PermissionPort,
    RuntimeActor,
    RuntimeActorType,
    RuntimeArtifactPublishRequest,
    RuntimeArtifactReadRequest,
    RuntimeArtifactResolveRequest,
    RuntimeCheckpoint,
    RuntimeOwnerType,
    RuntimePermissionOutcome,
    RuntimePermissionRequest,
    RuntimePermissionRisk,
    RuntimePrincipal,
    RuntimeRunIdentity,
    StaticPermissionPort,
)


def _identity(*, owner_id: str = "owner-1") -> RuntimeRunIdentity:
    owner = RuntimePrincipal("qq", RuntimeOwnerType.USER, owner_id)
    return RuntimeRunIdentity(
        run_id="run-service-port",
        turn_id="turn-service-port",
        correlation_id="correlation-service-port",
        actor=RuntimeActor(RuntimeActorType.USER, owner_id),
        owner=owner,
    )


@pytest.mark.asyncio
async def test_checkpoint_store_is_idempotent_monotonic_and_owner_scoped():
    store = InMemoryCheckpointStore()
    assert isinstance(store, CheckpointStore)
    first = RuntimeCheckpoint(
        checkpoint_id="checkpoint-1",
        identity=_identity(),
        sequence=1,
        schema_version=1,
        created_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
        payload=b'{"step":1}',
    )
    second = RuntimeCheckpoint(
        checkpoint_id="checkpoint-2",
        identity=_identity(),
        sequence=2,
        schema_version=1,
        created_at=datetime(2026, 8, 3, 0, 1, tzinfo=timezone.utc),
        payload=b'{"step":2}',
        parent_checkpoint_id="checkpoint-1",
    )

    assert await store.save(first) is first
    assert await store.save(first) is first
    assert await store.save(second) is second
    assert first.payload_sha256
    assert first.size_bytes == len(first.payload)
    assert (
        await store.load_latest(
            first.identity.run_id,
            owner=first.identity.owner,
        )
        is second
    )
    assert (
        await store.load(
            "checkpoint-1",
            owner=_identity(owner_id="other-owner").owner,
        )
        is None
    )

    stale = RuntimeCheckpoint(
        checkpoint_id="checkpoint-stale",
        identity=_identity(),
        sequence=2,
        schema_version=1,
        created_at=datetime(2026, 8, 3, 0, 2, tzinfo=timezone.utc),
        payload=b"stale",
    )
    with pytest.raises(ValueError, match="严格递增"):
        await store.save(stale)

    broken_parent = RuntimeCheckpoint(
        checkpoint_id="checkpoint-broken-parent",
        identity=_identity(),
        sequence=3,
        schema_version=1,
        created_at=datetime(2026, 8, 3, 0, 3, tzinfo=timezone.utc),
        payload=b"broken-parent",
        parent_checkpoint_id="checkpoint-1",
    )
    with pytest.raises(ValueError, match="当前最新 checkpoint"):
        await store.save(broken_parent)


@pytest.mark.asyncio
async def test_artifact_port_publishes_immutable_owner_scoped_content():
    port = InMemoryArtifactPort()
    assert isinstance(port, ArtifactPort)
    identity = _identity()
    data = "中文资产".encode()
    await port.stage_source(
        owner=identity.owner,
        workspace_id="workspace-1",
        virtual_path="reports/result.txt",
        data=data,
    )

    artifact = await port.publish(
        RuntimeArtifactPublishRequest(
            identity=identity,
            workspace_id="workspace-1",
            virtual_path="reports/result.txt",
            media_type="text/plain",
        )
    )
    first = await port.read(
        RuntimeArtifactReadRequest(
            artifact=artifact,
            owner=identity.owner,
            limit=4,
        )
    )
    second = await port.read(
        RuntimeArtifactReadRequest(
            artifact=artifact,
            owner=identity.owner,
            offset=4,
            limit=1024,
        )
    )

    assert artifact.uri == f"artifact://{artifact.artifact_id}"
    assert artifact.source_run_id == identity.run_id
    assert artifact.size_bytes == len(data)
    assert first.data + second.data == data
    assert first.eof is False
    assert second.eof is True
    assert await port.resolve(RuntimeArtifactResolveRequest(
        artifact_id=artifact.artifact_id,
        owner=identity.owner,
    )) == artifact
    with pytest.raises(PermissionError, match="owner 未授权"):
        await port.read(
            RuntimeArtifactReadRequest(
                artifact=artifact,
                owner=_identity(owner_id="other-owner").owner,
            )
        )
    with pytest.raises(ValueError, match="规范相对路径"):
        RuntimeArtifactPublishRequest(
            identity=identity,
            workspace_id="workspace-1",
            virtual_path="../secret",
        )
    with pytest.raises(ValueError, match="规范相对路径"):
        RuntimeArtifactPublishRequest(
            identity=identity,
            workspace_id="workspace-1",
            virtual_path="reports/./result.txt",
        )


@pytest.mark.asyncio
async def test_permission_port_defaults_to_deny_and_keeps_allow_once_receipt():
    port = StaticPermissionPort(
        {
            "workspace.write": RuntimePermissionOutcome.ALLOW_ONCE,
            "memory.read": RuntimePermissionOutcome.ALLOW,
        }
    )
    assert isinstance(port, PermissionPort)
    requested_at = datetime(2026, 8, 3, tzinfo=timezone.utc)
    write_request = RuntimePermissionRequest(
        request_id="permission-write-1",
        identity=_identity(),
        action="workspace.write",
        resource="workspace-1:/report.txt",
        risk=RuntimePermissionRisk.HIGH,
        requested_at=requested_at,
    )

    decision = await port.evaluate(write_request)
    assert decision.outcome is RuntimePermissionOutcome.ALLOW_ONCE
    assert decision.grant_id == "grant:permission-write-1"
    assert await port.evaluate(write_request) is decision

    denied = await port.evaluate(
        RuntimePermissionRequest(
            request_id="permission-network-1",
            identity=_identity(),
            action="network.connect",
            resource="https://example.invalid",
            risk=RuntimePermissionRisk.CRITICAL,
            requested_at=requested_at,
        )
    )
    assert denied.outcome is RuntimePermissionOutcome.DENY
    assert denied.grant_id == ""

    conflicting_request = RuntimePermissionRequest(
        request_id="permission-write-1",
        identity=_identity(),
        action="workspace.delete",
        resource="workspace-1:/report.txt",
        risk=RuntimePermissionRisk.CRITICAL,
        requested_at=requested_at,
    )
    with pytest.raises(ValueError, match="已绑定不同请求"):
        await port.evaluate(conflicting_request)
