"""Admin 写操作数据库唯一幂等账本测试。"""

from __future__ import annotations

import pytest


def test_admin_idempotency_claims_once_and_replays_safe_result(
    db_session,
):
    from core.admin.idempotency import (
        AdminIdempotencyInProgress,
        AdminIdempotencyService,
        admin_request_sha256,
    )

    service = AdminIdempotencyService(db_session)
    fingerprint = admin_request_sha256({
        "enabled": True,
        "reason": "测试幂等",
    })

    first = service.begin(
        request_id="admin-idempotency-0001",
        action="group_learning.feature.update",
        target_id="group_learning.enabled",
        request_sha256=fingerprint,
    )

    assert first.claimed is True
    with pytest.raises(AdminIdempotencyInProgress):
        service.begin(
            request_id="admin-idempotency-0001",
            action="group_learning.feature.update",
            target_id="group_learning.enabled",
            request_sha256=fingerprint,
        )

    service.succeed(
        request_id="admin-idempotency-0001",
        result={
            "ok": True,
            "replayed": False,
            "enabled": True,
        },
    )
    replay = service.begin(
        request_id="admin-idempotency-0001",
        action="group_learning.feature.update",
        target_id="group_learning.enabled",
        request_sha256=fingerprint,
    )

    assert replay.claimed is False
    assert replay.replay_result == {
        "ok": True,
        "replayed": False,
        "enabled": True,
    }


def test_admin_idempotency_rejects_payload_reuse_and_failed_retry(
    db_session,
):
    from core.admin.idempotency import (
        AdminIdempotencyConflict,
        AdminIdempotencyPreviousFailure,
        AdminIdempotencyService,
        admin_request_sha256,
    )

    service = AdminIdempotencyService(db_session)
    original = admin_request_sha256({"enabled": True})
    changed = admin_request_sha256({"enabled": False})
    service.begin(
        request_id="admin-idempotency-0002",
        action="group_learning.feature.update",
        target_id="group_learning.enabled",
        request_sha256=original,
    )

    with pytest.raises(AdminIdempotencyConflict):
        service.begin(
            request_id="admin-idempotency-0002",
            action="group_learning.feature.update",
            target_id="group_learning.enabled",
            request_sha256=changed,
        )

    service.fail(
        request_id="admin-idempotency-0002",
        error_code="validation_failed",
    )
    with pytest.raises(AdminIdempotencyPreviousFailure):
        service.begin(
            request_id="admin-idempotency-0002",
            action="group_learning.feature.update",
            target_id="group_learning.enabled",
            request_sha256=original,
        )
