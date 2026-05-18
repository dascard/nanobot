from contextvars import ContextVar, Token

_trace_id: ContextVar[str] = ContextVar("nanobot_trace_id", default="")
_run_id: ContextVar[str] = ContextVar("nanobot_run_id", default="")


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
