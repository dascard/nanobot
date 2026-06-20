from __future__ import annotations

import contextvars

import pytest

from core.async_bridge import run_awaitable_sync


trace_id = contextvars.ContextVar("trace_id", default="")


async def _return_value(value: str) -> str:
    return value


async def _raise_error() -> None:
    raise RuntimeError("boom")


async def _read_trace_id() -> str:
    return trace_id.get()


def test_run_awaitable_sync_runs_without_existing_event_loop():
    assert run_awaitable_sync(_return_value("ok")) == "ok"


def test_run_awaitable_sync_runs_when_asyncio_runner_is_unavailable(monkeypatch):
    import asyncio

    monkeypatch.delattr(asyncio, "Runner")

    assert run_awaitable_sync(_return_value("ok-without-runner")) == "ok-without-runner"


def test_run_awaitable_sync_does_not_depend_on_asyncio_runner(monkeypatch):
    import asyncio

    class ExplodingRunner:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("asyncio_runner_should_not_be_used")

    monkeypatch.setattr(asyncio, "Runner", ExplodingRunner, raising=False)

    assert run_awaitable_sync(_return_value("ok-with-manual-loop")) == "ok-with-manual-loop"


def test_run_awaitable_sync_propagates_exceptions():
    with pytest.raises(RuntimeError, match="boom"):
        run_awaitable_sync(_raise_error())


@pytest.mark.asyncio
async def test_run_awaitable_sync_works_inside_running_event_loop_with_contextvars():
    token = trace_id.set("trace-123")
    try:
        assert run_awaitable_sync(_read_trace_id()) == "trace-123"
    finally:
        trace_id.reset(token)
