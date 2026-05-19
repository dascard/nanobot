"""测试 llm_trace_context ContextVar 传递。"""

from core.llm_trace_context import llm_trace_id, llm_run_id, llm_source


def test_contextvar_default_empty():
    assert llm_trace_id.get() == ""
    assert llm_run_id.get() == ""
    assert llm_source.get() == ""


def test_contextvar_set_and_reset():
    tok_t = llm_trace_id.set("trace-123")
    tok_r = llm_run_id.set("run-456")
    tok_s = llm_source.set("replyer")

    assert llm_trace_id.get() == "trace-123"
    assert llm_run_id.get() == "run-456"
    assert llm_source.get() == "replyer"

    llm_source.reset(tok_s)
    llm_run_id.reset(tok_r)
    llm_trace_id.reset(tok_t)

    assert llm_trace_id.get() == ""
    assert llm_run_id.get() == ""
    assert llm_source.get() == ""
