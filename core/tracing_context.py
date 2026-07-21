from contextvars import ContextVar, Token

_trace_id: ContextVar[str] = ContextVar("nanobot_trace_id", default="")
_run_id: ContextVar[str] = ContextVar("nanobot_run_id", default="")
_tool_call_id: ContextVar[str] = ContextVar("nanobot_tool_call_id", default="")


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
