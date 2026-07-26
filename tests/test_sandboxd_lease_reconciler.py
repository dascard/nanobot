from __future__ import annotations

import json
from dataclasses import replace

import pytest

from core.sandbox.contracts import SandboxErrorCode, SandboxServiceError
from sandboxd.lease_backend import LeaseBackend
from sandboxd.lease_backend import PROFILE_LABEL
from sandboxd.lease_reconciler import LeaseReconciler
from sandboxd.lease_store import LeaseStore
from tests.test_sandboxd_lease_backend import (
    LEASE_ID,
    WORKSPACE_ID,
    _Container,
    _Image,
    _components,
    _ensure,
)


def test_controller_restart_recycles_only_old_owned_leases_and_publishes_ids(
    tmp_path,
):
    (
        config,
        workspace_files,
        asset_files,
        docker,
        old_store,
        old_backend,
    ) = _components(tmp_path)
    _ensure(old_backend, config)
    old_epoch = old_store.controller_epoch
    unrelated = _Container(
        docker.containers,
        {
            "name": "unrelated-service",
            "labels": {
                "com.nanobot.sandbox": "true",
                "com.nanobot.managed-by": "sandboxd",
            },
            "image": "sha256:" + "e" * 64,
        },
    )
    unrelated.start()
    docker.containers.values.append(unrelated)

    new_store = LeaseStore(
        config.data_root / "runtime" / ".sandboxd-leases"
    )
    new_store.start_controller(now_unix=2000.0)
    new_backend = LeaseBackend(
        config,
        docker_client=docker,
        workspace_files=workspace_files,
        asset_files=asset_files,
        lease_store=new_store,
        profile_image_resolver=lambda _profile_id: _Image(),
        network_policy=old_backend.network_policy,
        clock=lambda: 2000.0,
    )
    reconciler = LeaseReconciler(
        new_backend,
        new_store,
        interval_seconds=15,
        clock=lambda: 2000.0,
    )

    result = reconciler.recover_previous_controller()

    assert new_store.controller_epoch != old_epoch
    assert result == {
        "controller_epoch": new_store.controller_epoch,
        "recovered_lease_ids": [LEASE_ID],
        "recovered_process_ids": [],
        "failed_lease_ids": [],
        "cleaned_orphan_network_lease_ids": [],
    }
    assert new_store.startup_recovery()["recovered_lease_ids"] == [
        LEASE_ID
    ]
    assert docker.containers.values[0].removed is True
    assert unrelated.removed is False
    assert unrelated.attrs["State"]["Running"] is True
    assert workspace_files.layout.workspace_data_dir(WORKSPACE_ID).exists()
    assert workspace_files.layout.ensure_runtime(WORKSPACE_ID).exists()


def test_controller_restart_is_not_blocked_by_corrupt_lease_snapshot(
    tmp_path,
):
    (
        config,
        workspace_files,
        asset_files,
        docker,
        old_store,
        old_backend,
    ) = _components(tmp_path)
    _ensure(old_backend, config)
    snapshot = old_store.get(LEASE_ID)
    assert snapshot is not None
    malformed = snapshot.to_dict()
    malformed["created_at_unix"] = {}
    (
        old_store.leases_root / f"{LEASE_ID}.json"
    ).write_text(json.dumps(malformed), encoding="utf-8")

    new_store = LeaseStore(
        config.data_root / "runtime" / ".sandboxd-leases"
    )
    new_store.start_controller(now_unix=2000.0)
    new_backend = LeaseBackend(
        config,
        docker_client=docker,
        workspace_files=workspace_files,
        asset_files=asset_files,
        lease_store=new_store,
        profile_image_resolver=lambda _profile_id: _Image(),
        network_policy=old_backend.network_policy,
        clock=lambda: 2000.0,
    )

    result = LeaseReconciler(
        new_backend,
        new_store,
        interval_seconds=15,
        clock=lambda: 2000.0,
    ).recover_previous_controller()

    assert result["recovered_lease_ids"] == [LEASE_ID]
    assert result["failed_lease_ids"] == []
    assert docker.containers.values[0].removed is True


def test_controller_restart_recycles_owned_lease_with_invalid_detail_label(
    tmp_path,
):
    (
        config,
        workspace_files,
        asset_files,
        docker,
        _old_store,
        old_backend,
    ) = _components(tmp_path)
    _ensure(old_backend, config)
    docker.containers.values[0].attrs["Config"]["Labels"][
        PROFILE_LABEL
    ] = "INVALID!"

    new_store = LeaseStore(
        config.data_root / "runtime" / ".sandboxd-leases"
    )
    new_store.start_controller(now_unix=2000.0)
    new_backend = LeaseBackend(
        config,
        docker_client=docker,
        workspace_files=workspace_files,
        asset_files=asset_files,
        lease_store=new_store,
        profile_image_resolver=lambda _profile_id: _Image(),
        network_policy=old_backend.network_policy,
        clock=lambda: 2000.0,
    )

    result = LeaseReconciler(
        new_backend,
        new_store,
        interval_seconds=15,
        clock=lambda: 2000.0,
    ).recover_previous_controller()

    assert result["recovered_lease_ids"] == [LEASE_ID]
    assert result["failed_lease_ids"] == []
    assert docker.containers.values[0].removed is True


def test_idle_and_max_ttl_recycle_entire_lease_idempotently(tmp_path):
    now = {"value": 1000.0}
    (
        config,
        _workspace_files,
        _asset_files,
        _docker,
        store,
        backend,
    ) = _components(tmp_path, clock=lambda: now["value"])
    first = _ensure(backend, config)
    reconciler = LeaseReconciler(
        backend,
        store,
        interval_seconds=15,
        clock=lambda: now["value"],
    )

    now["value"] = float(first["idle_expires_at_unix"])
    idle_result = reconciler.reconcile()

    assert idle_result["recycled"] == [{
        "lease_id": LEASE_ID,
        "termination_reason": "lease_idle_ttl",
        "affected_process_ids": [],
    }]
    assert backend.get(LEASE_ID)["status"] == "missing"
    assert reconciler.reconcile()["recycled"] == []

    second_id = "sbxlease_max_ttl_test"
    second = _ensure(
        backend,
        config,
        request_id="lease_request_max_ttl",
        lease_id=second_id,
    )
    snapshot = store.get(second_id)
    assert snapshot is not None
    store.save(replace(
        snapshot,
        status="active",
        active_process_ids=("sbxrun_active_process",),
    ))
    now["value"] = float(second["max_expires_at_unix"])

    max_result = reconciler.reconcile()

    assert max_result["recycled"] == [{
        "lease_id": second_id,
        "termination_reason": "lease_max_ttl",
        "affected_process_ids": ["sbxrun_active_process"],
    }]


def test_reconciler_reentry_returns_stable_busy_error(tmp_path):
    (
        _config,
        _workspace_files,
        _asset_files,
        _docker,
        store,
        backend,
    ) = _components(tmp_path)
    reconciler = LeaseReconciler(
        backend,
        store,
        interval_seconds=15,
    )
    assert reconciler._reconcile_lock.acquire(blocking=False) is True
    try:
        with pytest.raises(SandboxServiceError) as busy:
            reconciler.reconcile()
    finally:
        reconciler._reconcile_lock.release()

    assert busy.value.code is SandboxErrorCode.SANDBOX_BUSY
