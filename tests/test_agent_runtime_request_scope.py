import asyncio
from types import SimpleNamespace

import pytest


def _runtime_context(session_id: str) -> dict[str, object]:
    return {
        "chat_type": "group",
        "runtime_chat_type": "group",
        "is_group": True,
        "is_super_user": False,
        "session_id": session_id,
        "group_id": session_id.removeprefix("group_"),
        "user_id": "",
        "platform": "qq",
        "trace_id": f"trace-{session_id}",
        "run_id": f"run-{session_id}",
    }


def test_runtime_context_scope_copies_freezes_and_resets_value():
    from core.agent_runtime.request_scope import (
        get_current_runtime_context,
        runtime_context_scope,
    )

    source = _runtime_context("group_1001")
    with runtime_context_scope(source) as current:
        source["session_id"] = "group_attacker"

        assert current["session_id"] == "group_1001"
        assert get_current_runtime_context() is current
        with pytest.raises(TypeError):
            current["session_id"] = "group_other"  # type: ignore[index]

    assert get_current_runtime_context() is None


@pytest.mark.asyncio
async def test_concurrent_runtime_contexts_are_isolated_in_tasks_and_threads():
    from core.agent_runtime.request_scope import (
        get_current_runtime_context,
        runtime_context_scope,
    )

    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def worker(index: int, session_id: str):
        with runtime_context_scope(_runtime_context(session_id)):
            ready[index].set()
            await release.wait()

            async def child_task():
                await asyncio.sleep(0)
                current = get_current_runtime_context()
                return str(current["session_id"]) if current is not None else ""

            task_value = await asyncio.create_task(child_task())
            thread_value = await asyncio.to_thread(
                lambda: str(
                    (get_current_runtime_context() or {}).get("session_id", "")
                )
            )
            own_value = str(
                (get_current_runtime_context() or {}).get("session_id", "")
            )
            return own_value, task_value, thread_value

    tasks = [
        asyncio.create_task(worker(0, "group_1061158966")),
        asyncio.create_task(worker(1, "group_1097666427")),
    ]
    await asyncio.gather(*(event.wait() for event in ready))
    release.set()
    results = await asyncio.gather(*tasks)

    assert results == [
        ("group_1061158966",) * 3,
        ("group_1097666427",) * 3,
    ]
    assert get_current_runtime_context() is None


def test_sandbox_tool_rejects_legacy_session_extra_and_uses_request_scope():
    from core.agent_runtime.request_scope import runtime_context_scope
    from core.sandbox.contracts import (
        SandboxErrorCode,
        SandboxServiceError,
    )
    from nanobot_kt.tools.sandbox import SandboxExecTool

    untrusted = SimpleNamespace(
        session=SimpleNamespace(
            extra={
                "nanobot_runtime_context": _runtime_context("group_attacker"),
            }
        )
    )

    with pytest.raises(SandboxServiceError) as exc_info:
        SandboxExecTool._trusted_runtime_context(untrusted)
    assert exc_info.value.code is SandboxErrorCode.AUTHORIZATION_FAILED

    with runtime_context_scope(_runtime_context("group_1061158966")):
        trusted = SandboxExecTool._trusted_runtime_context(untrusted)

    assert trusted["session_id"] == "group_1061158966"
    assert trusted["group_id"] == "1061158966"


def test_nanobot_bridges_use_distinct_internal_kt_session_keys():
    from nanobot_kt.bridge import NanobotBridge

    first = NanobotBridge()
    second = NanobotBridge()

    assert first._kt_session_key.startswith("nanobot-bridge-")
    assert second._kt_session_key.startswith("nanobot-bridge-")
    assert first._kt_session_key != second._kt_session_key


@pytest.mark.asyncio
async def test_bridge_start_failure_always_runs_session_cleanup(monkeypatch):
    from nanobot_kt.bridge import NanobotBridge

    bridge = NanobotBridge()
    cleanup_calls = []

    async def fail_start():
        raise RuntimeError("start failed")

    async def cleanup():
        cleanup_calls.append(bridge._kt_session_key)

    monkeypatch.setattr(bridge, "_start", fail_start)
    monkeypatch.setattr(bridge, "_cleanup_failed_start", cleanup)

    with pytest.raises(RuntimeError, match="start failed"):
        await bridge.start()

    assert cleanup_calls == [bridge._kt_session_key]
