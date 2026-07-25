from contextvars import ContextVar, Token

from core.telemetry.contracts import TelemetryCorrelation


_request_id: ContextVar[str] = ContextVar(
    "nanobot_request_id",
    default="",
)
_session_id: ContextVar[str] = ContextVar(
    "nanobot_session_id",
    default="",
)
_turn_id: ContextVar[str] = ContextVar("nanobot_turn_id", default="")
_trace_id: ContextVar[str] = ContextVar("nanobot_trace_id", default="")
_run_id: ContextVar[str] = ContextVar("nanobot_run_id", default="")
_task_id: ContextVar[str] = ContextVar("nanobot_task_id", default="")
_task_run_id: ContextVar[str] = ContextVar(
    "nanobot_task_run_id",
    default="",
)
_job_id: ContextVar[str] = ContextVar("nanobot_job_id", default="")
_tool_call_id: ContextVar[str] = ContextVar("nanobot_tool_call_id", default="")
_delivery_id: ContextVar[str] = ContextVar(
    "nanobot_delivery_id",
    default="",
)
_parent_job_id: ContextVar[str] = ContextVar(
    "nanobot_parent_job_id",
    default="",
)


RuntimeCorrelationTokens = tuple[
    tuple[ContextVar[str], Token[str]],
    ...,
]

_CORRELATION_VARS = {
    "request_id": _request_id,
    "session_id": _session_id,
    "turn_id": _turn_id,
    "trace_id": _trace_id,
    "run_id": _run_id,
    "task_id": _task_id,
    "task_run_id": _task_run_id,
    "job_id": _job_id,
    "tool_call_id": _tool_call_id,
    "delivery_id": _delivery_id,
    "parent_job_id": _parent_job_id,
}


def set_trace_context(trace_id: str, run_id: str) -> tuple[Token[str], Token[str]]:
    return _trace_id.set(trace_id or ""), _run_id.set(run_id or "")


def reset_trace_context(tokens: tuple[Token[str], Token[str]] | None) -> None:
    if not tokens:
        return
    trace_token, run_token = tokens
    _trace_id.reset(trace_token)
    _run_id.reset(run_token)


def get_trace_context() -> tuple[str, str]:
    return _trace_id.get(), _run_id.get()


def set_tool_trace_context(tool_call_id: str) -> Token[str]:
    return _tool_call_id.set(tool_call_id or "")


def reset_tool_trace_context(token: Token[str] | None) -> None:
    if token is not None:
        _tool_call_id.reset(token)


def get_tool_trace_context() -> str:
    return _tool_call_id.get()


def set_runtime_correlation(
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    trace_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    task_run_id: str | None = None,
    job_id: str | None = None,
    tool_call_id: str | None = None,
    delivery_id: str | None = None,
    parent_job_id: str | None = None,
) -> RuntimeCorrelationTokens:
    """只覆盖显式提供的关联字段，并返回可嵌套恢复的 Token。"""

    raw_values = {
        "request_id": request_id,
        "session_id": session_id,
        "turn_id": turn_id,
        "trace_id": trace_id,
        "run_id": run_id,
        "task_id": task_id,
        "task_run_id": task_run_id,
        "job_id": job_id,
        "tool_call_id": tool_call_id,
        "delivery_id": delivery_id,
        "parent_job_id": parent_job_id,
    }
    provided = {
        key: value
        for key, value in raw_values.items()
        if value is not None
    }
    normalized = TelemetryCorrelation(**provided)
    return tuple(
        (
            _CORRELATION_VARS[field_name],
            _CORRELATION_VARS[field_name].set(
                str(getattr(normalized, field_name) or "")
            ),
        )
        for field_name in provided
    )


def set_runtime_event_context(
    context: TelemetryCorrelation,
) -> RuntimeCorrelationTokens:
    if not isinstance(context, TelemetryCorrelation):
        raise TypeError("context 必须是 TelemetryCorrelation")
    return set_runtime_correlation(**context.to_dict())


def reset_runtime_correlation(
    tokens: RuntimeCorrelationTokens | None,
) -> None:
    if not tokens:
        return
    for variable, token in reversed(tokens):
        variable.reset(token)


def get_runtime_correlation() -> TelemetryCorrelation:
    return TelemetryCorrelation(**{
        field_name: variable.get()
        for field_name, variable in _CORRELATION_VARS.items()
    })


__all__ = [
    "RuntimeCorrelationTokens",
    "get_runtime_correlation",
    "get_tool_trace_context",
    "get_trace_context",
    "reset_runtime_correlation",
    "reset_tool_trace_context",
    "reset_trace_context",
    "set_runtime_correlation",
    "set_runtime_event_context",
    "set_tool_trace_context",
    "set_trace_context",
]
