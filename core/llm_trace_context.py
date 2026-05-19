"""LLM 请求链路上下文——通过 ContextVar 传递 trace_id/run_id/source 到深层调用。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

llm_trace_id: ContextVar[str] = ContextVar("llm_trace_id", default="")
llm_run_id: ContextVar[str] = ContextVar("llm_run_id", default="")
llm_source: ContextVar[str] = ContextVar("llm_source", default="")


def get_llm_trace_vars() -> tuple[str, str, str]:
    """返回当前上下文的 (trace_id, run_id, source)。"""
    return llm_trace_id.get(), llm_run_id.get(), llm_source.get()


@contextmanager
def llm_trace_scope(
    *,
    trace_id: str = "",
    run_id: str = "",
    source: str = "",
):
    """LLM 请求链路上下文管理器。

    内层 source 覆盖外层，trace_id/run_id 默认继承外层。
    支持嵌套使用。
    """
    prev_t = llm_trace_id.get()
    prev_r = llm_run_id.get()
    prev_s = llm_source.get()
    tok_t = llm_trace_id.set(trace_id or prev_t)
    tok_r = llm_run_id.set(run_id or prev_r)
    tok_s = llm_source.set(source or prev_s)
    try:
        yield
    finally:
        llm_source.reset(tok_s)
        llm_run_id.reset(tok_r)
        llm_trace_id.reset(tok_t)
