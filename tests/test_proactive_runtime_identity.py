import pytest


def test_proactive_runtime_lifecycle_requires_explicit_start():
    from core.proactive.runtime_identity import (
        ProactiveProcessIdentity,
        ProactiveRuntimeLifecycle,
        ProactiveRuntimeUnavailableError,
    )

    runtime = ProactiveRuntimeLifecycle()

    with pytest.raises(ProactiveRuntimeUnavailableError):
        runtime.require_identity()

    expected = ProactiveProcessIdentity(
        owner="proactive-outreach:test",
        writer_token="0123456789abcdef0123456789abcdef",
    )
    assert runtime.start(expected) == expected
    assert runtime.start() == expected
    assert runtime.require_identity() == expected

    runtime.stop()
    with pytest.raises(ProactiveRuntimeUnavailableError):
        runtime.require_identity()


def test_proactive_runtime_restart_creates_fresh_identity():
    from core.proactive.runtime_identity import ProactiveRuntimeLifecycle

    tokens = iter(("a" * 32, "b" * 64, "c" * 32, "d" * 64))
    runtime = ProactiveRuntimeLifecycle(
        pid_provider=lambda: 123,
        token_factory=lambda _size: next(tokens),
    )

    first = runtime.start()
    runtime.stop()
    second = runtime.start()

    assert first.owner != second.owner
    assert first.writer_token != second.writer_token


def test_proactive_runtime_rejects_identity_swap_while_started():
    from core.proactive.runtime_identity import (
        ProactiveProcessIdentity,
        ProactiveRuntimeLifecycle,
    )

    runtime = ProactiveRuntimeLifecycle()
    runtime.start(ProactiveProcessIdentity(
        owner="proactive-outreach:first",
        writer_token="1" * 32,
    ))

    with pytest.raises(RuntimeError, match="另一身份"):
        runtime.start(ProactiveProcessIdentity(
            owner="proactive-outreach:second",
            writer_token="2" * 32,
        ))
