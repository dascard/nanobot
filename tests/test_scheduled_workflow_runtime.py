"""统一定时任务生产回调绑定测试。"""

from __future__ import annotations

import pytest

from tests.async_helpers import run_async


@pytest.fixture(autouse=True)
def _clear_runtime_binding():
    from core.scheduled_workflow_runtime import (
        clear_scheduled_workflow_callbacks,
    )

    clear_scheduled_workflow_callbacks()
    yield
    clear_scheduled_workflow_callbacks()


def test_scheduled_workflow_callbacks_binding_is_explicit():
    from core.scheduled_workflow_runtime import (
        bind_scheduled_workflow_callbacks,
        create_scheduled_workflow_callbacks,
        scheduled_workflow_runtime_state,
    )

    callbacks = object()

    assert scheduled_workflow_runtime_state() == "stopped"
    assert create_scheduled_workflow_callbacks() is None

    bind_scheduled_workflow_callbacks(lambda: callbacks)

    assert scheduled_workflow_runtime_state() == "running"
    assert create_scheduled_workflow_callbacks() is callbacks


def test_scheduled_workflow_callbacks_reject_implicit_replacement():
    from core.scheduled_workflow_runtime import (
        bind_scheduled_workflow_callbacks,
    )

    bind_scheduled_workflow_callbacks(object)

    with pytest.raises(RuntimeError, match="已绑定"):
        bind_scheduled_workflow_callbacks(object)


def test_daily_digest_does_not_claim_before_agent_runtime_ready(
    monkeypatch,
):
    import core.daily_digest as daily_digest

    async def fail_if_called(**_kwargs):
        raise AssertionError("运行时未就绪时不得领取 execution")

    monkeypatch.setattr(
        daily_digest,
        "run_scheduled_task_workflow_worker",
        fail_if_called,
    )

    result = run_async(daily_digest.run_scheduled_task_workflows_once())

    assert result.claimed == 0
    assert result.succeeded == 0
    assert result.failed == 0
    assert result.blocked == 0
    assert result.ambiguous == 0
