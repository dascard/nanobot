"""LLM 请求链路上下文——通过 ContextVar 传递 trace_id/run_id/source 到深层调用。"""

from contextvars import ContextVar

llm_trace_id: ContextVar[str] = ContextVar("llm_trace_id", default="")
llm_run_id: ContextVar[str] = ContextVar("llm_run_id", default="")
llm_source: ContextVar[str] = ContextVar("llm_source", default="")
