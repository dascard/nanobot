from datetime import datetime, timezone

import pytest


@pytest.mark.parametrize("value", [True, 0, -1, float("nan"), float("inf")])
def test_positive_seconds_rejects_non_positive_or_non_finite(value):
    from core.fencing import positive_seconds

    with pytest.raises((TypeError, ValueError)):
        positive_seconds(value, field_name="lease_seconds")


def test_lease_deadline_preserves_datetime_timezone_semantics():
    from core.fencing import lease_deadline

    aware = datetime(2026, 7, 21, tzinfo=timezone.utc)
    naive = datetime(2026, 7, 21)
    assert (lease_deadline(aware, 15) - aware).total_seconds() == 15
    assert lease_deadline(aware, 15).tzinfo is timezone.utc
    assert (lease_deadline(naive, 15) - naive).total_seconds() == 15
    assert lease_deadline(naive, 15).tzinfo is None


def test_operation_timeout_must_be_strictly_inside_lease_window():
    from core.fencing import require_lease_exceeds_operation

    assert require_lease_exceeds_operation(
        operation_timeout_seconds=10,
        lease_seconds=11,
    ) == (10.0, 11.0)
    with pytest.raises(ValueError, match="严格大于"):
        require_lease_exceeds_operation(
            operation_timeout_seconds=10,
            lease_seconds=10,
        )


def test_lease_fence_requires_both_live_lease_and_constant_time_token_match():
    from core.fencing import FencingIdentity, LeaseFence

    now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    fence = LeaseFence(
        identity=FencingIdentity(owner="worker-a", token="a" * 64),
        expires_at=datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc),
    )
    assert fence.authorizes(owner="worker-a", token="a" * 64, now=now)
    assert not fence.authorizes(owner="worker-b", token="a" * 64, now=now)
    assert not fence.authorizes(owner="worker-a", token="b" * 64, now=now)
    assert not fence.authorizes(
        owner="worker-a",
        token="a" * 64,
        now=datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc),
    )
